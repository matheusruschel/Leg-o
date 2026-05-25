from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable

from ..models import SwiftFile

SKIP_DIRS = {"Pods", "Carthage", ".build", "DerivedData", ".git", "build", "node_modules"}
SKIP_FILE_PATTERNS = ("*Tests.swift", "*.generated.swift", "Package.swift")
SKIP_DIR_PATTERNS = ("*Tests", "*TestsUI", "*UITests")


def _gitignore_patterns(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    patterns: list[str] = []
    for raw in gi.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _matches_gitignore(rel: Path, patterns: Iterable[str]) -> bool:
    rel_str = str(rel)
    for pat in patterns:
        if pat.endswith("/"):
            if rel_str.startswith(pat) or f"/{pat}" in f"/{rel_str}":
                return True
        if fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(rel.name, pat):
            return True
    return False


def _language_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".swift":
        return "swift"
    if suffix in {".m", ".h", ".mm"}:
        return "objc"
    return None


def _should_skip_dir(d: Path) -> bool:
    if d.name in SKIP_DIRS:
        return True
    return any(fnmatch.fnmatch(d.name, pat) for pat in SKIP_DIR_PATTERNS)


def _should_skip_file(f: Path) -> bool:
    return any(fnmatch.fnmatch(f.name, pat) for pat in SKIP_FILE_PATTERNS)


def discover_files(path: Path, include_objc: bool = False) -> list[SwiftFile]:
    """Recursively discover Swift (and optionally Objective-C) source files.

    Skips test files, generated files, and common dependency directories.
    Honors .gitignore at `path` if present.
    """
    path = Path(path)
    if path.is_file():
        lang = _language_for(path)
        if lang == "swift" or (lang == "objc" and include_objc):
            return [SwiftFile(path=path, content=path.read_text(errors="ignore"), language=lang)]
        return []

    gitignore = _gitignore_patterns(path)
    results: list[SwiftFile] = []

    for entry in path.rglob("*"):
        if entry.is_dir():
            continue
        if any(_should_skip_dir(p) for p in entry.relative_to(path).parents):
            continue
        if _should_skip_file(entry):
            continue
        lang = _language_for(entry)
        if lang is None:
            continue
        if lang == "objc" and not include_objc:
            continue
        rel = entry.relative_to(path)
        if gitignore and _matches_gitignore(rel, gitignore):
            continue
        try:
            content = entry.read_text(errors="ignore")
        except OSError:
            continue
        results.append(SwiftFile(path=entry, content=content, language=lang))

    results.sort(key=lambda f: str(f.path))
    return results
