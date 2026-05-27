from __future__ import annotations

import re
from pathlib import Path

from .models import ClassMetadata, MethodMetadata


# ---------------------------------------------------------------------------
# UI types
# ---------------------------------------------------------------------------

_UI_SUPERCLASS_PREFIXES = (
    "UIViewController", "UIView", "UITableView", "UICollectionView",
    "UITableViewCell", "UICollectionViewCell", "UITableViewHeaderFooterView",
    "UINavigationController", "UITabBarController", "UISplitViewController",
    "UIPageViewController", "UIWindow", "UIControl", "UIButton", "UILabel",
    "UIImageView", "UITextView", "UITextField", "UIScrollView", "UIStackView",
    "NSView", "NSViewController", "NSWindow", "NSWindowController",
)
_SWIFTUI_PROTOCOLS = {"View", "App", "Scene"}


def is_ui_type(meta: ClassMetadata) -> str | None:
    """If the class looks like a view/view-controller, return a short reason."""
    sc = meta.superclass or ""
    for prefix in _UI_SUPERCLASS_PREFIXES:
        if sc == prefix or sc.startswith(prefix):
            return f"UI type (inherits {sc})"
    if set(meta.protocols) & _SWIFTUI_PROTOCOLS:
        return f"SwiftUI type (conforms to {sorted(set(meta.protocols) & _SWIFTUI_PROTOCOLS)})"
    return None


# ---------------------------------------------------------------------------
# Pure data holders
# ---------------------------------------------------------------------------

_CONTROL_FLOW_RE = re.compile(r"\b(if|for|while|guard|switch|do|try|throw|return)\b")
_TRIVIAL_LINE_BUDGET = 2  # methods with bodies this short are considered trivial


def is_data_holder(meta: ClassMetadata) -> str | None:
    """Conservative heuristic: structs/enums whose methods are all trivial."""
    if meta.kind not in {"struct", "enum"}:
        return None
    if not meta.methods:
        return "data holder (no methods)"
    if all(_is_trivial(m) for m in meta.methods):
        return "data holder (all methods trivial)"
    return None


def _is_trivial(m: MethodMetadata) -> bool:
    if m.line_count <= _TRIVIAL_LINE_BUDGET:
        return True
    body = (m.body_text or "").strip("{}\n \t")
    if not body:
        return True
    if not _CONTROL_FLOW_RE.search(body):
        # Body has substance lines but no control flow — likely a simple property build / pass-through.
        return m.line_count <= 4
    return False


# ---------------------------------------------------------------------------
# Empty methods
# ---------------------------------------------------------------------------

def is_empty_method(m: MethodMetadata) -> bool:
    body = (m.body_text or "").strip()
    if not body:
        return True
    stripped = body.strip("{}\n \t")
    return stripped == ""


def filter_non_empty_methods(meta: ClassMetadata) -> list[MethodMetadata]:
    return [m for m in meta.methods if not is_empty_method(m)]


# ---------------------------------------------------------------------------
# Existing test files
# ---------------------------------------------------------------------------

_TEST_FILE_RE = re.compile(r"^(.+?)Tests\.(swift|m)$")
# Captures whatever follows "test_" or "test" (camelCase). Greedy enough to grab the method name
# segment but stops at the first underscore-separated section.
_SWIFT_TEST_METHOD_RE = re.compile(r"func\s+test_?([A-Za-z][A-Za-z0-9]*)")
_OBJC_TEST_METHOD_RE = re.compile(r"-\s*\(void\)\s*test_?([A-Za-z][A-Za-z0-9]*)")


def find_existing_test_files(test_dir: Path) -> dict[str, Path]:
    """Return a mapping {class_name: path_to_test_file} for *Tests.{swift,m} files.

    Class name is derived from the file name (e.g., FooTests.swift -> Foo).
    """
    test_dir = Path(test_dir)
    if not test_dir.exists():
        return {}
    result: dict[str, Path] = {}
    for entry in test_dir.rglob("*"):
        if not entry.is_file():
            continue
        m = _TEST_FILE_RE.match(entry.name)
        if m:
            result[m.group(1)] = entry
    return result


def covered_methods_in(test_file: Path) -> set[str]:
    """Pull method names from existing test method names like test_fooBar_scenario_..."""
    try:
        text = Path(test_file).read_text(errors="ignore")
    except OSError:
        return set()
    names: set[str] = set()
    for match in _SWIFT_TEST_METHOD_RE.finditer(text):
        names.add(_normalize_method_token(match.group(1)))
    for match in _OBJC_TEST_METHOD_RE.finditer(text):
        names.add(_normalize_method_token(match.group(1)))
    return names


def _normalize_method_token(token: str) -> str:
    """test_fetchUser → fetchUser; testFetchUser → fetchUser."""
    if not token:
        return token
    return token[0].lower() + token[1:]
