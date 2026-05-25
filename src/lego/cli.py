from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .scanner import file_discovery, swift_scanner, objc_scanner


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
@click.option("--output", "output", type=click.Path(path_type=Path), default=None)
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", default=None)
def analyze(path: Path, output: Path | None, api_key: str | None) -> None:
    """Layers 1+2: analyze testability (not yet implemented)."""
    click.echo("analyze: not implemented yet", err=True)
    sys.exit(2)


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
