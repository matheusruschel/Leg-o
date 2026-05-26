from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .scanner import file_discovery, swift_scanner, objc_scanner
from .analyzer import assess_testability, filter_testable, prioritize_methods, apply_limit
from .llm import ClaudeClient


@click.group()
@click.version_option()
def main() -> None:
    """lego — iOS legacy test generator."""


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
@click.option("--output", "output", required=True, type=click.Path(path_type=Path))
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None)
@click.option("--model", default="claude-sonnet-4-20250514")
@click.option("--xcodeproj", type=click.Path(path_type=Path), default=None)
@click.option("--scheme", default=None)
@click.option("--max-retries", default=3, type=int)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--single-file", is_flag=True, default=False)
@click.option("--batch-size", default=10, type=int)
@click.option("--include-objc", is_flag=True, default=False)
def generate(**_: object) -> None:
    """Full pipeline (not yet implemented)."""
    click.echo("generate: not implemented yet", err=True)
    sys.exit(2)


@main.command()
@click.option("--path", "path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None)
def estimate(path: Path, api_key: str | None) -> None:
    """Cost/time estimate (not yet implemented)."""
    click.echo("estimate: not implemented yet", err=True)
    sys.exit(2)


if __name__ == "__main__":
    main()
