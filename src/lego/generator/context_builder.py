from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..models import (
    ClassMetadata,
    ContextBundle,
    SwiftFile,
    TestabilityResult,
)


MAX_CONTEXT_CHARS = 100_000

# tier order: protocols first, then types referenced in method signatures, then everything else.
_TIER_PROTOCOL = 0
_TIER_SIGNATURE_TYPE = 1
_TIER_OTHER = 2


def build_context(
    target: ClassMetadata,
    all_files: list[SwiftFile],
    analysis: TestabilityResult | None,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> ContextBundle:
    by_path = {sf.path: sf for sf in all_files}
    target_file = by_path.get(target.file_path)
    if target_file is None:
        raise ValueError(f"target file not in provided files: {target.file_path}")
    target_content = target_file.content

    needed_types = _types_to_resolve(target, analysis)
    signature_types = _types_in_method_signatures(target)

    candidates: list[tuple[int, Path, str]] = []
    for sf in all_files:
        if sf.path == target.file_path:
            continue
        matched = _types_defined_in(sf.content) & needed_types
        if not matched:
            continue
        tier = _classify_tier(matched, sf.content, signature_types)
        candidates.append((tier, sf.path, sf.content))

    # Sort: protocols → signature types → others; then by smaller files first to fit more in budget.
    candidates.sort(key=lambda c: (c[0], len(c[2])))

    budget = max_chars - len(target_content)
    related: dict[str, str] = {}
    for _tier, path, content in candidates:
        if budget <= 0:
            break
        if len(content) <= budget:
            related[str(path)] = content
            budget -= len(content)

    summary = _summarize_analysis(analysis) if analysis else ""

    return ContextBundle(
        target_file=target.file_path,
        target_class=target.name,
        target_content=target_content,
        related_contents=related,
        analysis_summary=summary,
    )


def _types_to_resolve(target: ClassMetadata, analysis: TestabilityResult | None) -> set[str]:
    types: set[str] = set()
    if target.superclass:
        types.add(target.superclass)
    types.update(target.protocols)
    for prop in target.properties:
        if prop.type:
            types.update(_extract_type_names(prop.type))
    for method in target.methods:
        for param in method.parameters:
            if param.type:
                types.update(_extract_type_names(param.type))
        if method.return_type:
            types.update(_extract_type_names(method.return_type))
    for dep in target.dependencies:
        types.add(dep.type_name)
    if analysis:
        for dep in analysis.dependencies:
            types.add(dep.type_name)
    # Strip known built-ins / system framework types — they don't live in user files.
    return {t for t in types if t and t not in _SYSTEM_TYPES}


def _types_in_method_signatures(target: ClassMetadata) -> set[str]:
    types: set[str] = set()
    for method in target.methods:
        for param in method.parameters:
            if param.type:
                types.update(_extract_type_names(param.type))
        if method.return_type:
            types.update(_extract_type_names(method.return_type))
    return types


_TYPE_TOKEN_RE = re.compile(r"[A-Z][A-Za-z0-9_]+")


def _extract_type_names(type_expr: str) -> Iterable[str]:
    # Pull out capitalized identifiers from things like "Result<User, Error>?" or "[String: Foo]".
    return _TYPE_TOKEN_RE.findall(type_expr)


_DEF_RE = re.compile(
    r"\b(?:class|struct|enum|protocol|actor|typealias)\s+([A-Z][A-Za-z0-9_]+)"
)


def _types_defined_in(content: str) -> set[str]:
    return set(_DEF_RE.findall(content))


_PROTOCOL_DEF_RE = re.compile(r"\bprotocol\s+([A-Z][A-Za-z0-9_]+)")


def _classify_tier(matched: set[str], content: str, signature_types: set[str]) -> int:
    protocols_in_file = set(_PROTOCOL_DEF_RE.findall(content))
    if matched & protocols_in_file:
        return _TIER_PROTOCOL
    if matched & signature_types:
        return _TIER_SIGNATURE_TYPE
    return _TIER_OTHER


def _summarize_analysis(a: TestabilityResult) -> str:
    lines = [f"class: {a.class_name} (testable={a.testable}, score={a.testability_score})"]
    if a.dependencies:
        lines.append("dependencies:")
        for d in a.dependencies:
            lines.append(
                f"  - {d.type_name}: mockable={d.mockable} strategy={d.mock_strategy}"
                + (f" ({d.reason})" if d.reason else "")
            )
    if a.blocking_issues:
        lines.append("blocking_issues: " + "; ".join(a.blocking_issues))
    if a.refactoring_suggestions:
        lines.append("refactoring_suggestions: " + "; ".join(a.refactoring_suggestions))
    if a.testable_methods:
        lines.append("testable_methods: " + ", ".join(a.testable_methods))
    return "\n".join(lines)


_SYSTEM_TYPES = {
    "String", "Int", "Double", "Float", "Bool", "Character",
    "Array", "Dictionary", "Set", "Optional", "Result",
    "Void", "Any", "AnyObject", "Self",
    "Data", "Date", "URL", "URLRequest", "URLResponse", "URLSession",
    "URLSessionDataTask", "URLSessionTask",
    "Error", "NSError", "NSObject",
    "UIView", "UIViewController", "UIImage", "UIColor", "UIFont",
    "UITableView", "UITableViewCell", "UICollectionView",
    "Notification", "NotificationCenter", "UserDefaults",
    "DispatchQueue", "DispatchGroup", "OperationQueue",
    "JSONDecoder", "JSONEncoder", "JSONSerialization",
    "Codable", "Decodable", "Encodable", "Equatable", "Hashable", "Comparable",
}
