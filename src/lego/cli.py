from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click


def _setup_logging(verbose: bool = False) -> None:
    """Send lego's log records to stderr with timestamps so users see progress."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    root = logging.getLogger("lego")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False

from .scanner import file_discovery, swift_scanner, objc_scanner
from .analyzer import assess_testability, filter_testable, prioritize_methods, apply_limit
from .llm import ClaudeClient
from .orchestrator import PipelineConfig, autodetect_workspace, run_pipeline
from .models import GenerationPlan
from .pod_detector import classes_skipped_by_pod_import, resolve_pod_modules
from .reporter import generate_report


@click.group()
@click.version_option()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug-level logging.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress progress logs (errors still print).")
@click.pass_context
def main(ctx: click.Context, verbose: bool, quiet: bool) -> None:
    """lego — iOS legacy test generator."""
    if quiet:
        logging.getLogger("lego").setLevel(logging.WARNING)
    else:
        _setup_logging(verbose=verbose)


@main.command()
@click.option("--path", "path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output", type=click.Path(path_type=Path), default=None,
              help="Write AST JSON to this file. Defaults to stdout.")
@click.option("--include-objc", is_flag=True, default=False, help="Also scan .m/.h files.")
def scan(path: Path, output: Path | None, include_objc: bool) -> None:
    """Layer 1: tree-sitter scan. Outputs AST metadata as JSON."""
    files = file_discovery.discover_files(path, include_objc=include_objc)
    all_classes = []
    for sf in files:
        if sf.language == "swift":
            all_classes.extend(swift_scanner.scan_file(sf))
        else:
            all_classes.extend(objc_scanner.scan_file(sf))

    payload = [c.model_dump(mode="json") for c in all_classes]
    text = json.dumps(payload, indent=2, default=str)
    if output is not None:
        output.write_text(text)
        click.echo(f"Wrote {len(all_classes)} class entries from {len(files)} files to {output}")
    else:
        click.echo(text)


@main.command()
@click.option("--path", "path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output", type=click.Path(path_type=Path), default=None,
              help="Write the testability report JSON to this file. Defaults to stdout.")
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", required=True)
@click.option("--model", default="claude-sonnet-4-5-20250929")
@click.option("--include-objc", is_flag=True, default=False)
@click.option("--batch-size", default=50, type=int)
@click.option("--limit", default=50, type=int, help="Max methods to include in the prioritized list.")
def analyze(
    path: Path,
    output: Path | None,
    api_key: str,
    model: str,
    include_objc: bool,
    batch_size: int,
    limit: int,
) -> None:
    """Layers 1+2: scan files, then assess testability and prioritize methods."""
    files = file_discovery.discover_files(path, include_objc=include_objc)
    all_classes = []
    for sf in files:
        if sf.language == "swift":
            all_classes.extend(swift_scanner.scan_file(sf))
        else:
            all_classes.extend(objc_scanner.scan_file(sf))

    client = ClaudeClient(api_key=api_key, model=model)
    results = assess_testability(all_classes, client, batch_size=batch_size)
    testable = filter_testable(results)
    ranked = apply_limit(prioritize_methods(testable, client), limit)

    report = {
        "files_scanned": len(files),
        "classes_analyzed": len(all_classes),
        "assessments": [r.model_dump(mode="json") for r in results],
        "testable": [r.class_name for r in testable],
        "prioritized_methods": [m.model_dump(mode="json") for m in ranked],
        "token_usage": client.tracker.report(),
    }
    text = json.dumps(report, indent=2, default=str)
    if output is not None:
        output.write_text(text)
        click.echo(
            f"Analyzed {len(all_classes)} classes from {len(files)} files; "
            f"{len(testable)} testable. Wrote report to {output}"
        )
    else:
        click.echo(text)


@main.command()
@click.option("--path", "path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output", required=True, type=click.Path(path_type=Path),
              help="Directory to write generated test files (and REPORT.md).")
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", required=True)
@click.option("--model", default="claude-sonnet-4-5-20250929")
@click.option("--module-name", default="App", help="Swift module name for @testable import.")
@click.option("--xcodeproj", type=click.Path(path_type=Path), default=None,
              help="Path to .xcodeproj. If a sibling .xcworkspace exists, it is preferred.")
@click.option("--xcworkspace", type=click.Path(path_type=Path), default=None,
              help="Path to .xcworkspace (required for CocoaPods/SPM-workspace projects).")
@click.option("--scheme", default=None)
@click.option("--test-target-dir", type=click.Path(path_type=Path), default=None)
@click.option("--test-target", "test_target_name", default=None,
              help="Xcode target name to auto-add generated files to (e.g., WSLTests). "
                   "Requires --xcodeproj (or derives one next to --xcworkspace).")
@click.option("--destination", default=None,
              help="xcodebuild -destination string. Default: 'platform=iOS Simulator,name=iPhone 16'. "
                   "Check `xcrun simctl list devices available` for installed simulators.")
@click.option("--max-retries", default=3, type=int)
@click.option("--dry-run", is_flag=True, default=False,
              help="Run scan + analyze; skip API generation calls.")
@click.option("--single-file", type=click.Path(exists=True, path_type=Path), default=None,
              help="Generate tests only for classes in this file (skips analyze).")
@click.option("--batch-size", default=50, type=int)
@click.option("--method-limit", default=50, type=int)
@click.option("--include-objc", is_flag=True, default=False)
@click.option("--no-skip-pods", is_flag=True, default=False,
              help="Don't auto-skip classes that import CocoaPods modules.")
@click.option("--skip-module", "skip_modules", multiple=True,
              help="Additional module name(s) to treat as un-mockable; classes importing these are skipped.")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip the pre-generation confirm prompt (for CI / scripting).")
def generate(
    path: Path,
    output: Path,
    api_key: str,
    model: str,
    module_name: str,
    xcodeproj: Path | None,
    xcworkspace: Path | None,
    scheme: str | None,
    test_target_dir: Path | None,
    test_target_name: str | None,
    destination: str | None,
    max_retries: int,
    dry_run: bool,
    single_file: Path | None,
    batch_size: int,
    method_limit: int,
    include_objc: bool,
    no_skip_pods: bool,
    skip_modules: tuple[str, ...],
    yes: bool,
) -> None:
    """Run the full pipeline: scan → analyze → generate → (validate) → report."""
    if xcworkspace is None and xcodeproj is not None:
        detected = autodetect_workspace(xcodeproj)
        if detected is not None:
            click.echo(f"(auto-detected sibling workspace: {detected}; using -workspace)", err=True)
            xcworkspace = detected

    # If user asked us to add files to a target, we need an xcodeproj path.
    # Derive one from the workspace if they didn't pass it.
    add_to_xcodeproj: Path | None = None
    if test_target_name is not None:
        add_to_xcodeproj = xcodeproj
        if add_to_xcodeproj is None and xcworkspace is not None:
            candidate = xcworkspace.with_suffix(".xcodeproj")
            if candidate.exists():
                add_to_xcodeproj = candidate
        if add_to_xcodeproj is None:
            raise click.UsageError(
                "--test-target requires --xcodeproj (or a workspace with a sibling .xcodeproj)"
            )
    config = PipelineConfig(
        path=path,
        output_dir=output,
        module_name=module_name,
        include_objc=include_objc,
        batch_size=batch_size,
        method_limit=method_limit,
        xcodeproj=xcodeproj,
        xcworkspace=xcworkspace,
        scheme=scheme,
        test_target_dir=test_target_dir,
        test_target_name=test_target_name,
        add_to_target_xcodeproj=add_to_xcodeproj,
        destination=destination,
        max_retries=max_retries,
        dry_run=dry_run,
        single_file=single_file,
        skip_pod_dependent=not no_skip_pods,
        extra_skip_modules=list(skip_modules),
    )
    output.mkdir(parents=True, exist_ok=True)
    client = ClaudeClient(api_key=api_key, model=model)
    confirm = None if yes or dry_run else _interactive_confirm
    report = run_pipeline(config, client, confirm_callback=confirm)
    md = generate_report(report)
    (output / "REPORT.md").write_text(md)
    click.echo(md)
    if xcodeproj is None and not dry_run:
        click.echo(
            "\n(no --xcodeproj provided; tests were generated but not validated)",
            err=True,
        )


def _interactive_confirm(plan: GenerationPlan):
    """Print the per-class plan + cost, let the user exclude items, then confirm.

    Returns False to abort, True to keep everything, or a set of class names to keep.
    """
    click.echo("\n--- Generation plan ---")
    click.echo(f"Model: {plan.model}")
    click.echo(f"Classes to generate: {len(plan.items)}\n")
    for idx, item in enumerate(plan.items, start=1):
        methods = ", ".join(item.methods) if item.methods else "(no methods)"
        click.echo(
            f"  [{idx:>2}] {item.class_name} "
            f"({len(item.methods)} method(s)): {methods}"
        )
        click.echo(
            f"        ~{item.estimated_input_tokens} in + {item.estimated_output_tokens} out tokens, "
            f"~${item.estimated_cost_usd:.4f}"
        )
    click.echo("")
    click.echo(
        f"Totals: ~{plan.total_input_tokens} input + {plan.total_output_tokens} output tokens"
    )
    click.echo(f"Analysis cost already spent: ${plan.analysis_cost_so_far_usd:.4f}")
    click.echo(f"Projected generation cost:   ${plan.total_estimated_cost_usd:.4f}")
    click.echo(
        f"Projected total:             "
        f"${plan.analysis_cost_so_far_usd + plan.total_estimated_cost_usd:.4f}"
    )

    raw = click.prompt(
        "\nNumbers to EXCLUDE (comma-separated, e.g. '1,3'); empty to keep all",
        default="", show_default=False,
    )
    excluded_indexes = _parse_exclusion_input(raw, count=len(plan.items))
    if excluded_indexes is None:
        click.echo("Invalid input; aborting.")
        return False

    if excluded_indexes:
        kept_names = {
            item.class_name for i, item in enumerate(plan.items, start=1)
            if i not in excluded_indexes
        }
        if not kept_names:
            click.echo("All items excluded; aborting.")
            return False
        click.echo(
            f"\nKeeping {len(kept_names)} of {len(plan.items)} classes "
            f"(excluding {len(excluded_indexes)})."
        )
        if not click.confirm("Proceed with generation?", default=False):
            return False
        return kept_names

    return click.confirm("\nProceed with generation?", default=False)


def _parse_exclusion_input(raw: str, count: int) -> set[int] | None:
    """Parse '1,3,5' into {1,3,5}. Return None on any parse error or out-of-range."""
    raw = (raw or "").strip()
    if not raw:
        return set()
    result: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            n = int(token)
        except ValueError:
            return None
        if n < 1 or n > count:
            return None
        result.add(n)
    return result


# rough per-method estimate; assumes Claude Sonnet 4.x pricing & typical sizes.
_EST_INPUT_TOKENS_PER_METHOD = 8000   # context bundle + prompt
_EST_OUTPUT_TOKENS_PER_METHOD = 1500  # generated test code


@main.command()
@click.option("--path", "path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", required=True)
@click.option("--model", default="claude-sonnet-4-5-20250929")
@click.option("--include-objc", is_flag=True, default=False)
@click.option("--batch-size", default=50, type=int)
@click.option("--method-limit", default=50, type=int)
@click.option("--no-skip-pods", is_flag=True, default=False,
              help="Don't auto-skip classes that import CocoaPods modules.")
@click.option("--skip-module", "skip_modules", multiple=True,
              help="Additional module name(s) to treat as un-mockable; classes importing these are skipped.")
def estimate(
    path: Path,
    api_key: str,
    model: str,
    include_objc: bool,
    batch_size: int,
    method_limit: int,
    no_skip_pods: bool,
    skip_modules: tuple[str, ...],
) -> None:
    """Scan + analyze + project the cost of generating tests, without generating."""
    files = file_discovery.discover_files(path, include_objc=include_objc)
    all_classes = []
    for sf in files:
        if sf.language == "swift":
            all_classes.extend(swift_scanner.scan_file(sf))
        else:
            all_classes.extend(objc_scanner.scan_file(sf))

    pod_modules = resolve_pod_modules(
        path, extra_modules=list(skip_modules), enabled=not no_skip_pods,
    )
    pod_skipped = 0
    if pod_modules:
        all_classes, skipped = classes_skipped_by_pod_import(all_classes, pod_modules)
        pod_skipped = len(skipped)

    client = ClaudeClient(api_key=api_key, model=model)
    assessments = assess_testability(all_classes, client, batch_size=batch_size)
    testable = filter_testable(assessments)
    ranked = apply_limit(prioritize_methods(testable, client), method_limit)

    in_rate, out_rate = client.tracker.pricing.get(
        client.tracker.model, client.tracker.pricing["default"]
    )
    projected_gen_cost = len(ranked) * (
        _EST_INPUT_TOKENS_PER_METHOD * in_rate / 1_000_000
        + _EST_OUTPUT_TOKENS_PER_METHOD * out_rate / 1_000_000
    )
    so_far = client.tracker.estimated_cost()

    summary = {
        "files_scanned": len(files),
        "total_classes": len(all_classes) + pod_skipped,
        "pod_skipped_classes": pod_skipped,
        "classes_analyzed": len(all_classes),
        "testable_classes": len(testable),
        "prioritized_methods": len(ranked),
        "analysis_cost_so_far_usd": round(so_far, 6),
        "projected_generation_cost_usd": round(projected_gen_cost, 4),
        "projected_total_usd": round(so_far + projected_gen_cost, 4),
        "model": model,
    }
    click.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
