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
from .filters import (
    covered_methods_in,
    detect_test_framework,
    find_existing_test_files,
    is_data_holder,
    is_empty_method,
    is_ui_type,
)
from .generator import build_context, generate_tests
from .generator.test_generator import (
    InvalidTestOutput,
    augment_tests,
    write_test_file,
)
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
from .xcode_project import XcodeProjectError, add_files_to_target

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
    # Auto-add generated files to an xcodeproj target (writes project.pbxproj)
    test_target_name: Optional[str] = None
    add_to_target_xcodeproj: Optional[Path] = None
    # Modes
    dry_run: bool = False
    single_file: Optional[Path] = None
    # Pod handling
    skip_pod_dependent: bool = True
    extra_skip_modules: list[str] = field(default_factory=list)
    # Heuristic filters
    skip_ui_types: bool = True
    skip_data_holders: bool = True
    regenerate_existing: bool = False  # False → augment existing test files instead
    # Test framework: None → auto-detect from existing test files, else 'xctest' / 'swift_testing'
    framework: Optional[str] = None


def build_generation_plan(
    targets: list[GenerationTarget],
    claude_client: ClaudeClient,
) -> GenerationPlan:
    """Project per-class token usage + cost for the upcoming generation step.

    `targets` is the output of _resolve_generation_targets — each entry is
    (class_meta, methods_to_generate, existing_test_path_or_None). Augment-mode
    entries (existing path present) cost more input tokens because we send the
    existing test file content along too.
    """
    tracker = claude_client.tracker
    in_rate, out_rate = tracker.pricing.get(tracker.model, tracker.pricing["default"])

    items: list[GenerationPlanItem] = []
    for meta, methods, existing_path in targets:
        augment = existing_path is not None
        in_tok = _EST_INPUT_TOKENS_PER_CLASS
        if augment:
            try:
                in_tok += max(0, len(existing_path.read_text(errors="ignore")) // 4)
            except OSError:
                pass
        out_tok = max(1, len(methods)) * _EST_OUTPUT_TOKENS_PER_METHOD
        cost = in_tok * in_rate / 1_000_000 + out_tok * out_rate / 1_000_000
        items.append(GenerationPlanItem(
            class_name=meta.name,
            file_path=meta.file_path,
            methods=methods,
            estimated_input_tokens=in_tok,
            estimated_output_tokens=out_tok,
            estimated_cost_usd=round(cost, 4),
            mode="augment" if augment else "generate",
            existing_test_path=existing_path,
        ))

    return GenerationPlan(
        items=items,
        total_input_tokens=sum(i.estimated_input_tokens for i in items),
        total_output_tokens=sum(i.estimated_output_tokens for i in items),
        total_estimated_cost_usd=round(sum(i.estimated_cost_usd for i in items), 4),
        analysis_cost_so_far_usd=round(tracker.estimated_cost(), 6),
        model=tracker.model,
    )


GenerationTarget = tuple[ClassMetadata, list[str], "Path | None"]


def _resolve_generation_targets(
    target_classes: list[ClassMetadata],
    ranked: list[PrioritizedMethod],
    config: PipelineConfig,
    report: PipelineReport,
) -> list[GenerationTarget]:
    """Decide per class: (re)generate fresh, augment existing tests, or skip."""
    existing_tests_dir = config.test_target_dir or config.output_dir
    existing_test_files = find_existing_test_files(existing_tests_dir)

    targets: list[GenerationTarget] = []
    skipped_already_covered = 0
    for meta in target_classes:
        methods = _methods_for_class(meta.name, ranked) or [m.name for m in meta.methods]
        empty_method_names = {m.name for m in meta.methods if is_empty_method(m)}
        methods = [m for m in methods if m not in empty_method_names]
        if not methods:
            report.class_results.append(ClassResult(
                class_name=meta.name, file_path=meta.file_path,
                status="skipped", error_summary="no non-empty methods to test",
            ))
            continue
        existing_path = existing_test_files.get(meta.name)
        if existing_path and not config.regenerate_existing:
            covered = covered_methods_in(existing_path, candidate_methods=methods)
            uncovered = [m for m in methods if m not in covered]
            if not uncovered:
                report.class_results.append(ClassResult(
                    class_name=meta.name, file_path=meta.file_path,
                    status="skipped",
                    error_summary="all methods already covered by existing tests",
                    output_path=existing_path,
                ))
                skipped_already_covered += 1
                continue
            targets.append((meta, uncovered, existing_path))
        else:
            targets.append((meta, methods, None))

    if skipped_already_covered:
        log.info("skipped %d class(es) that already have full test coverage",
                 skipped_already_covered)
    return targets


def run_pipeline(
    config: PipelineConfig,
    claude_client: ClaudeClient,
    compile_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
    confirm_callback: Optional[Callable[[GenerationPlan], "object"]] = None,
) -> PipelineReport:
    """Run the lego pipeline. confirm_callback (if provided) is called once with
    the generation plan before any generation API calls. Its return value:

      - falsy / None / False  → abort, no generation
      - True                  → proceed with the full plan (all classes)
      - set[str] / list[str]  → proceed but only generate for these class names
    """
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

        classes = _apply_heuristic_filters(classes, config, report)

        report.classes_analyzed = len(classes)
        log.info("scanned %d files, %d classes after pod filter", len(files), len(classes))

        if config.single_file is not None:
            target_classes = _classes_in_file(classes, config.single_file)
            assessments: list[TestabilityResult] = []
            ranked: list[PrioritizedMethod] = []
        else:
            batches = (len(classes) + config.batch_size - 1) // config.batch_size
            log.info("analyzing %d classes in %d batch(es) of %d ...",
                     len(classes), batches, config.batch_size)
            assessments = assess_testability(classes, claude_client, batch_size=config.batch_size)
            testable = filter_testable(assessments)
            report.testable_classes = len(testable)
            report.classes_needing_refactor = len(assessments) - len(testable)
            log.info("analyze complete: %d testable, %d need refactor",
                     len(testable), report.classes_needing_refactor)
            log.info("prioritizing methods across %d testable classes ...", len(testable))
            ranked_all = prioritize_methods(testable, claude_client)
            ranked = apply_limit(ranked_all, config.method_limit)
            report.top_recommendations = ranked
            log.info("prioritized %d methods (limit %d)", len(ranked), config.method_limit)
            report.refactoring_needed = _refactor_summary(assessments, testable)
            target_classes = _group_by_class(classes, ranked)

        targets = _resolve_generation_targets(target_classes, ranked, config, report)

        if confirm_callback is not None and not config.dry_run and targets:
            plan = build_generation_plan(targets, claude_client)
            decision = confirm_callback(plan)
            if not decision:
                log.info("user declined generation plan; aborting before generate")
                report.token_usage = claude_client.tracker.report()
                report.estimated_cost = claude_client.tracker.estimated_cost()
                return report
            if isinstance(decision, (set, list, tuple)):
                keep = set(decision)
                excluded_targets = [t for t in targets if t[0].name not in keep]
                targets = [t for t in targets if t[0].name in keep]
                for meta, _m, _p in excluded_targets:
                    report.class_results.append(ClassResult(
                        class_name=meta.name, status="skipped",
                        error_summary="excluded by user at confirm step",
                    ))
                log.info("user kept %d of %d classes",
                         len(targets), len(targets) + len(excluded_targets))

        generated_results = _generate_for_targets(
            targets, files, assessments, config, claude_client,
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


def _apply_heuristic_filters(
    classes: list[ClassMetadata],
    config: PipelineConfig,
    report: PipelineReport,
) -> list[ClassMetadata]:
    """Drop UI types and pure data holders before paying analyze tokens."""
    kept: list[ClassMetadata] = []
    ui_skipped = data_skipped = 0
    for c in classes:
        if config.skip_ui_types:
            reason = is_ui_type(c)
            if reason:
                report.class_results.append(ClassResult(
                    class_name=c.name, file_path=c.file_path,
                    status="skipped", error_summary=reason,
                ))
                ui_skipped += 1
                continue
        if config.skip_data_holders:
            reason = is_data_holder(c)
            if reason:
                report.class_results.append(ClassResult(
                    class_name=c.name, file_path=c.file_path,
                    status="skipped", error_summary=reason,
                ))
                data_skipped += 1
                continue
        kept.append(c)
    if ui_skipped or data_skipped:
        log.info("heuristic filters skipped %d UI types, %d data holders",
                 ui_skipped, data_skipped)
    return kept


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
    log.info("scanning %s ...", config.path)
    files = file_discovery.discover_files(config.path, include_objc=config.include_objc)
    classes: list[ClassMetadata] = []
    for sf in files:
        if sf.language == "swift":
            classes.extend(swift_scanner.scan_file(sf))
        else:
            classes.extend(objc_scanner.scan_file(sf))
    log.info("scan complete: %d files, %d classes", len(files), len(classes))
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


def _generate_for_targets(
    targets: list[GenerationTarget],
    files: list[SwiftFile],
    assessments: list[TestabilityResult],
    config: PipelineConfig,
    claude_client: ClaudeClient,
    compile_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
) -> list[ClassResult]:
    by_class_name = {a.class_name: a for a in assessments}
    by_file_path = {sf.path: sf for sf in files}
    results: list[ClassResult] = []

    framework = config.framework
    if framework is None:
        framework = detect_test_framework(config.test_target_dir or config.output_dir)
        log.info("test framework: %s (auto-detected)", framework)
    else:
        log.info("test framework: %s (explicit)", framework)

    total = len(targets)
    for idx, (meta, methods, existing_path) in enumerate(targets, start=1):
        analysis = by_class_name.get(meta.name)
        mode = "augmenting" if existing_path else "generating"
        log.info("[%d/%d] %s tests for %s (%d method(s))",
                 idx, total, mode, meta.name, len(methods))

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
            if existing_path is not None:
                existing_content = existing_path.read_text(errors="ignore")
                generated = augment_tests(
                    bundle, existing_content, existing_path,
                    claude_client, methods, module_name=config.module_name,
                )
            else:
                generated = generate_tests(
                    bundle, claude_client, methods,
                    module_name=config.module_name, framework=framework,
                )
        except InvalidTestOutput as e:
            results.append(ClassResult(
                class_name=meta.name, methods=methods, status="generation_failed",
                error_summary=f"invalid generator output: {e}",
            ))
            continue

        output_path = write_test_file(generated, config.output_dir)
        if config.add_to_target_xcodeproj and config.test_target_name:
            try:
                add_files_to_target(
                    config.add_to_target_xcodeproj,
                    config.test_target_name,
                    [output_path],
                )
            except XcodeProjectError as e:
                log.warning("could not add %s to target: %s", output_path, e)
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
