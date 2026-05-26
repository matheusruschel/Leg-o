from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.llm.token_tracker import TokenTracker
from lego.orchestrator import PipelineConfig, build_generation_plan, run_pipeline
from lego.reporter import generate_report
from lego.scanner import file_discovery, swift_scanner


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_TEST = (FIXTURES / "generator" / "SampleNetworkServiceTests.swift").read_text()


def _make_client(tracker: TokenTracker | None = None) -> MagicMock:
    client = MagicMock()
    client.tracker = tracker or TokenTracker(model="default")
    return client


def _assessment_response(class_names):
    return {
        "assessments": [
            {
                "class_name": name,
                "testable": True,
                "testability_score": 80,
                "dependencies": [],
                "blocking_issues": [],
                "refactoring_suggestions": [],
                "testable_methods": ["foo"],
                "untestable_methods": [],
            }
            for name in class_names
        ]
    }


def _prioritize_response(class_names):
    return [
        {"class_name": name, "method_name": "foo", "priority_score": 90, "reason": "logic"}
        for name in class_names
    ]


def test_run_pipeline_end_to_end_with_validation_skipped(tmp_path: Path):
    # Use the existing Swift fixtures dir; analyze + generate via mocked Claude.
    src = FIXTURES  # scanner will skip our Tests.swift fixture by name
    output_dir = tmp_path / "out"

    client = _make_client()
    # Call sequence: assess (1 batch) → prioritize → N generations (one per class).
    # The scanner finds several classes; assess returns testable for two of them.
    client.call_json.side_effect = [
        _assessment_response(["NetworkService", "SimpleModel"]),
        _prioritize_response(["NetworkService", "SimpleModel"]),
    ]
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=src,
        output_dir=output_dir,
        module_name="MyApp",
        method_limit=10,
    )
    report = run_pipeline(config, client)

    assert report.files_scanned >= 1
    assert report.classes_analyzed >= 1
    assert report.testable_classes == 2
    assert report.tests_generated >= 1
    # No xcodeproj → validation skipped → status "generated_unverified"
    statuses = {r.status for r in report.class_results}
    assert "generated_unverified" in statuses
    # Output files exist on disk
    written = list(output_dir.glob("*Tests.swift"))
    assert len(written) >= 1
    # Token usage populated
    assert "total_input_tokens" in report.token_usage


def test_run_pipeline_dry_run_skips_generation(tmp_path: Path):
    client = _make_client()
    client.call_json.side_effect = [
        _assessment_response(["NetworkService"]),
        _prioritize_response(["NetworkService"]),
    ]

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        dry_run=True,
        method_limit=10,
    )
    report = run_pipeline(config, client)

    client.call.assert_not_called()  # no generation API calls in dry-run
    assert all(r.status == "skipped" for r in report.class_results)


def test_run_pipeline_single_file_skips_analysis(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
    )
    report = run_pipeline(config, client)

    client.call_json.assert_not_called()  # no analyze calls
    assert report.classes_analyzed >= 1
    # All generated classes came from the single file
    for r in report.class_results:
        assert r.output_path is not None
        assert r.output_path.parent == tmp_path / "out"


def test_run_pipeline_invokes_validation_when_configured(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()
    client.call.return_value = SAMPLE_TEST

    compile_fn = MagicMock(return_value=(True, "** BUILD SUCCEEDED **"))
    run_fn = MagicMock(return_value=(True, "** TEST SUCCEEDED **"))

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
        xcodeproj=tmp_path / "App.xcodeproj",
        scheme="App",
        test_target_dir=tmp_path / "out",
        max_retries=1,
    )
    report = run_pipeline(config, client, compile_fn=compile_fn, run_fn=run_fn)

    compile_fn.assert_called()
    run_fn.assert_called()
    # First-pass rate should be 1.0 (compiled cleanly on first try)
    assert report.first_pass_compile_rate == 1.0
    assert report.final_pass_rate == 1.0
    assert all(r.status == "passed" for r in report.class_results)


def test_run_pipeline_skips_classes_importing_pod_modules(tmp_path: Path):
    # Lay out a tiny project: two Swift files, one imports a pod module from the lockfile.
    project = tmp_path / "proj"
    src = project / "Sources"
    src.mkdir(parents=True)
    (project / "Podfile.lock").write_text(
        "PODS:\n  - Alamofire (5.10.0)\n  - AppAuth (1.7.5)\n"
    )
    (src / "UsesPod.swift").write_text(
        "import Foundation\nimport Alamofire\nclass UsesPod {}\n"
    )
    (src / "Clean.swift").write_text(
        "import Foundation\nclass Clean {\n    func foo() {}\n}\n"
    )

    client = _make_client()
    client.call_json.side_effect = [
        _assessment_response(["Clean"]),
        _prioritize_response(["Clean"]),
    ]
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=src,
        output_dir=tmp_path / "out",
        module_name="MyApp",
        method_limit=10,
    )
    report = run_pipeline(config, client)

    by_name = {r.class_name: r for r in report.class_results}
    assert "UsesPod" in by_name
    assert by_name["UsesPod"].status == "skipped"
    assert "Alamofire" in (by_name["UsesPod"].error_summary or "")
    # The clean class still went through generation
    assert "Clean" in by_name
    assert by_name["Clean"].status != "skipped"


def test_build_generation_plan_computes_per_class_cost():
    # Load real ClassMetadata from a fixture so methods are populated.
    files = file_discovery.discover_files(FIXTURES)
    network = [c for sf in files if sf.path.name == "network_service.swift"
               for c in swift_scanner.scan_file(sf)]
    assert network, "fixture should produce at least one class"

    client = _make_client()  # tracker defaults to "default" pricing 3/15
    ranked = []  # no prioritized methods → fall back to all class methods
    plan = build_generation_plan(network, ranked, client)

    assert len(plan.items) == len(network)
    for item in plan.items:
        assert item.estimated_input_tokens > 0
        assert item.estimated_output_tokens > 0
        assert item.estimated_cost_usd > 0
    assert plan.total_estimated_cost_usd == pytest.approx(
        sum(i.estimated_cost_usd for i in plan.items), abs=1e-4
    )


def test_run_pipeline_aborts_when_confirm_returns_false(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
    )
    confirm = MagicMock(return_value=False)
    report = run_pipeline(config, client, confirm_callback=confirm)

    confirm.assert_called_once()
    # No generation API calls should have happened
    client.call.assert_not_called()
    # No generated class results recorded
    assert report.tests_generated == 0


def test_run_pipeline_proceeds_when_confirm_returns_true(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
    )
    confirm = MagicMock(return_value=True)
    report = run_pipeline(config, client, confirm_callback=confirm)

    confirm.assert_called_once()
    client.call.assert_called()
    assert report.tests_generated >= 1


def test_run_pipeline_filters_classes_when_confirm_returns_subset(tmp_path: Path):
    # Set up a project with two classes; confirm callback returns only one of them.
    src = tmp_path / "src"
    src.mkdir()
    (src / "A.swift").write_text("import Foundation\nclass A {\n  func foo() {}\n}\n")
    (src / "B.swift").write_text("import Foundation\nclass B {\n  func bar() {}\n}\n")

    client = _make_client()
    client.call_json.side_effect = [
        _assessment_response(["A", "B"]),
        _prioritize_response(["A", "B"]),
    ]
    client.call.return_value = SAMPLE_TEST

    confirm = MagicMock(return_value={"A"})  # keep only A
    config = PipelineConfig(
        path=src,
        output_dir=tmp_path / "out",
        method_limit=10,
    )
    report = run_pipeline(config, client, confirm_callback=confirm)

    by_name = {r.class_name: r for r in report.class_results}
    # A was generated
    assert by_name["A"].status != "skipped"
    # B was excluded
    assert by_name["B"].status == "skipped"
    assert "excluded by user" in (by_name["B"].error_summary or "")
    # Claude was called once for generation (only for A)
    assert client.call.call_count == 1


def test_run_pipeline_skips_confirm_in_dry_run(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
        dry_run=True,
    )
    confirm = MagicMock(return_value=True)
    run_pipeline(config, client, confirm_callback=confirm)

    confirm.assert_not_called()  # dry-run skips the confirm gate


def test_generate_report_renders_markdown_sections(tmp_path: Path):
    target_file = FIXTURES / "network_service.swift"
    client = _make_client()
    client.call.return_value = SAMPLE_TEST

    config = PipelineConfig(
        path=FIXTURES,
        output_dir=tmp_path / "out",
        single_file=target_file,
    )
    report = run_pipeline(config, client)
    md = generate_report(report)

    assert "# Test Generation Report" in md
    assert "## Summary" in md
    assert "## Per-Class Results" in md
    assert "## Token Usage" in md
    # Should mention at least one class name from the fixture
    assert "NetworkService" in md
