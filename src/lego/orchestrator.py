from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .analyzer import (
    apply_limit,
    assess_testability,
    filter_testable,
    prioritize_methods,
)
from .generator import build_context, generate_tests
from .generator.test_generator import InvalidTestOutput, write_test_file
from .llm.client import ClaudeClient
from .models import (
    ClassMetadata,
    ClassResult,
    GenerationPlan,
    GenerationPlanItem,
    PipelineReport,
    PrioritizedMethod,
    SwiftFile,
    TestabilityResult,
)
from .pod_detector import (
    classes_skipped_by_pod_import,
    resolve_pod_modules,
)
from .scanner import file_discovery, objc_scanner, swift_scanner
from .validator.feedback_loop import FeedbackLoopConfig, validate_and_fix

log = logging.getLogger(__name__)


# Rough heuristics for the pre-flight cost estimate. Input is context-dominated
# (full target file + related files, capped at MAX_CONTEXT_CHARS), so it doesn't
# scale much with method count. Output does scale with the number of generated tests.
_EST_INPUT_TOKENS_PER_CLASS = 10_000
_EST_OUTPUT_TOKENS_PER_METHOD = 1_500


@dataclass
class PipelineConfig:
    path: Path
    output_dir: Path
    module_name: str = "App"
    include_objc: bool = False
    batch_size: int = 50
    method_limit: int = 50
    # Validation
    xcodeproj: Optional[Path] = None
    xcworkspace: Optional[Path] = None
    scheme: Optional[str] = None
    test_target_dir: Optional[Path] = None
    max_retries: int = 3
    destination: Optional[str] = None  # None → use FeedbackLoopConfig default
    # Modes
    dry_run: bool = False
    single_file: Optional[Path] = None
    # Pod handling
    skip_pod_dependent: bool = True
    extra_skip_modules: list[str] = field(default_factory=list)


def build_generation_plan(
    target_classes: list[ClassMetadata],
    ranked: list[PrioritizedMethod],
    claude_client: ClaudeClient,
) -> GenerationPlan:
    """Project per-class token usage + cost for the upcoming generation step."""
    tracker = claude_client.tracker
    in_rate, out_rate = tracker.pricing.get(tracker.model, tracker.pricing["default"])

    items: list[GenerationPlanItem] = []
    for meta in target_classes:
        methods = _methods_for_class(meta.name, ranked) or [m.name for m in meta.methods]
        in_tok = _EST_INPUT_TOKENS_PER_CLASS
        out_tok = max(1, len(methods)) * _EST_OUTPUT_TOKENS_PER_METHOD
        cost = in_tok * in_rate / 1_000_000 + out_tok * out_rate / 1_000_000
        items.append(GenerationPlanItem(
            class_name=meta.name,
            file_path=meta.file_path,
            methods=methods,
            estimated_input_tokens=in_tok,
            estimated_output_tokens=out_tok,
            estimated_cost_usd=round(cost, 4),
        ))

    return GenerationPlan(
        items=items,
        total_input_tokens=sum(i.estimated_input_tokens for i in items),
        total_output_tokens=sum(i.estimated_output_tokens for i in items),
        total_estimated_cost_usd=round(sum(i.estimated_cost_usd for i in items), 4),
        analysis_cost_so_far_usd=round(tracker.estimated_cost(), 6),
        model=tracker.model,
    )


def run_pipeline(
    config: PipelineConfig,
    claude_client: ClaudeClient,
    compile_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
    confirm_callback: Optional[Callable[[GenerationPlan], bool]] = None,
) -> PipelineReport:
    report = PipelineReport()
    try:
        files, classes = _scan(config)
        report.files_scanned = len(files)

        pod_modules = _resolve_pod_modules(config)
        if pod_modules:
            classes, skipped = classes_skipped_by_pod_import(classes, pod_modules)
            for c, matched in skipped:
                report.class_results.append(ClassResult(
                    class_name=c.name,
                    file_path=c.file_path,
                    status="skipped",
                    error_summary=f"depends on pod module(s): {', '.join(sorted(matched))}",
                ))
            log.info("skipped %d classes that import pod modules", len(skipped))

        report.classes_analyzed = len(classes)
        log.info("scanned %d files, %d classes after pod filter", len(files), len(classes))

        if config.single_file is not None:
            target_classes = _classes_in_file(classes, config.single_file)
            assessments: list[TestabilityResult] = []
            ranked: list[PrioritizedMethod] = []
        else:
            assessments = assess_testability(classes, claude_client, batch_size=config.batch_size)
            testable = filter_testable(assessments)
            report.testable_classes = len(testable)
            report.classes_needing_refactor = len(assessments) - len(testable)
            ranked_all = prioritize_methods(testable, claude_client)
            ranked = apply_limit(ranked_all, config.method_limit)
            report.top_recommendations = ranked
            log.info("%d testable, %d methods prioritized", len(testable), len(ranked))
            report.refactoring_needed = _refactor_summary(assessments, testable)
            target_classes = _group_by_class(classes, ranked)

        if confirm_callback is not None and not config.dry_run and target_classes:
            plan = build_generation_plan(target_classes, ranked, claude_client)
            if not confirm_callback(plan):
                log.info("user declined generation plan; aborting before generate")
                report.token_usage = claude_client.tracker.report()
                report.estimated_cost = claude_client.tracker.estimated_cost()
                return report

        generated_results = _generate_for_classes(
            target_classes, files, assessments, ranked, config, claude_client,
            compile_fn=compile_fn, run_fn=run_fn,
        )
        report.class_results.extend(generated_results)
        report.tests_generated = sum(
            1 for r in generated_results
            if r.status not in {"generation_failed", "skipped"}
        )
        _compute_rates(report)
    except KeyboardInterrupt:
        log.warning("interrupted; returning partial results")

    report.token_usage = claude_client.tracker.report()
    report.estimated_cost = claude_client.tracker.estimated_cost()
    return report


def autodetect_workspace(xcodeproj: Path) -> Path | None:
    """If a sibling .xcworkspace exists next to the .xcodeproj, return it."""
    proj = Path(xcodeproj)
    sibling = proj.with_suffix(".xcworkspace")
    return sibling if sibling.exists() else None


def _resolve_pod_modules(config: PipelineConfig) -> set[str]:
    return resolve_pod_modules(
        config.path,
        extra_modules=config.extra_skip_modules,
        enabled=config.skip_pod_dependent,
    )


def _scan(config: PipelineConfig) -> tuple[list[SwiftFile], list[ClassMetadata]]:
    files = file_discovery.discover_files(config.path, include_objc=config.include_objc)
    classes: list[ClassMetadata] = []
    for sf in files:
        if sf.language == "swift":
            classes.extend(swift_scanner.scan_file(sf))
        else:
            classes.extend(objc_scanner.scan_file(sf))
    return files, classes


def _classes_in_file(classes: list[ClassMetadata], target_path: Path) -> list[ClassMetadata]:
    target = Path(target_path).resolve()
    return [c for c in classes if Path(c.file_path).resolve() == target]


def _group_by_class(
    all_classes: list[ClassMetadata],
    ranked: list[PrioritizedMethod],
) -> list[ClassMetadata]:
    """Return the ClassMetadata for every class that has at least one prioritized method,
    in priority order (highest-scoring class first)."""
    by_name = {c.name: c for c in all_classes}
    seen: set[str] = set()
    ordered: list[ClassMetadata] = []
    for method in ranked:
        if method.class_name in seen:
            continue
        seen.add(method.class_name)
        meta = by_name.get(method.class_name)
        if meta is not None:
            ordered.append(meta)
    return ordered


def _methods_for_class(class_name: str, ranked: list[PrioritizedMethod]) -> list[str]:
    return [m.method_name for m in ranked if m.class_name == class_name]


def _generate_for_classes(
    target_classes: list[ClassMetadata],
    files: list[SwiftFile],
    assessments: list[TestabilityResult],
    ranked: list[PrioritizedMethod],
    config: PipelineConfig,
    claude_client: ClaudeClient,
    compile_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
) -> list[ClassResult]:
    by_class_name = {a.class_name: a for a in assessments}
    by_file_path = {sf.path: sf for sf in files}
    results: list[ClassResult] = []

    for idx, meta in enumerate(target_classes, start=1):
        methods = _methods_for_class(meta.name, ranked) or [m.name for m in meta.methods]
        analysis = by_class_name.get(meta.name)
        log.info("generating tests for %s (%d/%d)", meta.name, idx, len(target_classes))

        try:
            bundle = build_context(meta, files, analysis)
        except ValueError as e:
            results.append(ClassResult(
                class_name=meta.name, methods=methods, status="generation_failed",
                error_summary=str(e),
            ))
            continue

        if config.dry_run:
            log.info("[dry-run] would generate tests for %s with methods %s", meta.name, methods)
            results.append(ClassResult(
                class_name=meta.name, methods=methods, status="skipped",
                error_summary="dry-run",
            ))
            continue

        try:
            generated = generate_tests(bundle, claude_client, methods, module_name=config.module_name)
        except InvalidTestOutput as e:
            results.append(ClassResult(
                class_name=meta.name, methods=methods, status="generation_failed",
                error_summary=f"invalid generator output: {e}",
            ))
            continue

        output_path = write_test_file(generated, config.output_dir)
        result = ClassResult(
            class_name=meta.name, methods=methods, status="generated_unverified",
            output_path=output_path,
        )

        source_file = by_file_path.get(meta.file_path)
        source_content = source_file.content if source_file else ""

        loop_kwargs = dict(
            xcodeproj=config.xcodeproj,
            xcworkspace=config.xcworkspace,
            scheme=config.scheme,
            test_target_dir=config.test_target_dir or config.output_dir,
            max_retries=config.max_retries,
        )
        if config.destination is not None:
            loop_kwargs["destination"] = config.destination
        loop_config = FeedbackLoopConfig(**loop_kwargs)
        validation = validate_and_fix(
            generated, source_content, loop_config, claude_client,
            compile_fn=compile_fn, run_fn=run_fn,
        )
        result.validation = validation
        result.retries = validation.retry_count

        if validation.skipped:
            result.status = "generated_unverified"
        elif validation.passed:
            result.status = "passed"
        elif validation.compiled:
            result.status = "test_failed"
            result.error_summary = _summarize_iteration_errors(validation)
        else:
            result.status = "compile_failed"
            result.error_summary = _summarize_iteration_errors(validation)

        results.append(result)

    return results


def _summarize_iteration_errors(validation) -> str:
    if not validation.iterations:
        return ""
    last = validation.iterations[-1]
    if not last.errors:
        return last.raw_output.splitlines()[-1] if last.raw_output else ""
    return "; ".join(e.message for e in last.errors[:3])


def _refactor_summary(
    assessments: list[TestabilityResult],
    testable: list[TestabilityResult],
) -> list[dict]:
    testable_names = {r.class_name for r in testable}
    return [
        {
            "class_name": a.class_name,
            "blocking_issues": list(a.blocking_issues),
            "refactoring_suggestions": list(a.refactoring_suggestions),
        }
        for a in assessments
        if a.class_name not in testable_names
    ]


def _compute_rates(report: PipelineReport) -> None:
    validated = [
        r for r in report.class_results
        if r.validation is not None and not r.validation.skipped
    ]
    if not validated:
        report.first_pass_compile_rate = 0.0
        report.final_pass_rate = 0.0
        report.average_retries = 0.0
        return

    first_pass = sum(
        1 for r in validated
        if r.validation.iterations
        and r.validation.iterations[0].step == "compile"
        and r.validation.iterations[0].success
    )
    passed = sum(1 for r in validated if r.status == "passed")
    report.first_pass_compile_rate = first_pass / len(validated)
    report.final_pass_rate = passed / len(validated)
    report.average_retries = sum(r.retries for r in validated) / len(validated)
