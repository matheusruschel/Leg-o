from __future__ import annotations

import re
from pathlib import Path

from .models import ClassMetadata


# Lines like "  - PodName (1.2.3):" or "  - PodName/Subspec (1.2.3):"
_POD_LINE_RE = re.compile(r"^\s*-\s+([A-Za-z0-9_]+)(?:/[^\s]+)?\s+\(")


def find_podfile_lock(project_path: Path) -> Path | None:
    """Walk up from project_path looking for a Podfile.lock."""
    p = Path(project_path).resolve()
    if p.is_file():
        p = p.parent
    for candidate in [p, *p.parents]:
        lockfile = candidate / "Podfile.lock"
        if lockfile.exists():
            return lockfile
    return None


def parse_pod_modules(podfile_lock: Path) -> set[str]:
    """Extract top-level pod module names from a Podfile.lock."""
    text = Path(podfile_lock).read_text(errors="ignore")
    pods: set[str] = set()
    in_pods_section = False
    for line in text.splitlines():
        if line.startswith("PODS:"):
            in_pods_section = True
            continue
        if in_pods_section and line and not line.startswith(" ") and not line.startswith("-"):
            break  # left the PODS: section
        m = _POD_LINE_RE.match(line)
        if m:
            pods.add(m.group(1))
    return pods


_OBJC_ANGLE_IMPORT_RE = re.compile(r"#\s*(?:import|include)\s*<([A-Za-z0-9_]+)\s*/")
_OBJC_MODULE_IMPORT_RE = re.compile(r"@\s*import\s+([A-Za-z0-9_]+)")


def normalize_import(token: str) -> str | None:
    """Return the module name from a raw scanner-extracted import token.

    Swift scanner stores bare module names ("Alamofire"); ObjC stores the full
    directive ("#import <Alamofire/Alamofire.h>" or "@import Alamofire;").
    Quoted-string ObjC imports ("Foo.h") are file-local — they return None.
    """
    token = token.strip()
    if not token:
        return None
    m = _OBJC_ANGLE_IMPORT_RE.match(token)
    if m:
        return m.group(1)
    m = _OBJC_MODULE_IMPORT_RE.match(token)
    if m:
        return m.group(1)
    if token.startswith("#") or token.startswith("@import"):
        # Quoted-form #import "Foo.h" or anything else we don't recognize.
        return None
    # Already a bare module name (Swift case).
    return token


def classes_skipped_by_pod_import(
    classes: list[ClassMetadata],
    pod_modules: set[str],
) -> tuple[list[ClassMetadata], list[tuple[ClassMetadata, set[str]]]]:
    """Split classes into (kept, skipped) based on whether their imports reference
    any pod-provided module. `skipped` carries the matched module names for reporting."""
    if not pod_modules:
        return classes, []
    kept: list[ClassMetadata] = []
    skipped: list[tuple[ClassMetadata, set[str]]] = []
    for c in classes:
        modules = {m for m in (normalize_import(t) for t in c.imports) if m}
        matched = modules & pod_modules
        if matched:
            skipped.append((c, matched))
        else:
            kept.append(c)
    return kept, skipped
