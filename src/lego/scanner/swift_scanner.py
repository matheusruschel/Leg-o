"""Swift AST extraction via tree-sitter.

Walks a Swift parse tree and produces ClassMetadata records for every
class / struct / enum / actor / protocol / extension declaration found
in the file, including methods, properties, parameters, and imports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from ..models import (
    AccessLevel,
    ClassMetadata,
    DependencyInfo,
    InjectionStyle,
    KindLiteral,
    MethodMetadata,
    ParameterMetadata,
    PropertyMetadata,
    SwiftFile,
)
from ._ts import node_text, swift_parser


ACCESS_KEYWORDS: set[str] = {"open", "public", "internal", "fileprivate", "private", "package"}

# Node type names produced by tree-sitter-swift for the top-level type declarations.
TYPE_DECL_NODES: dict[str, KindLiteral] = {
    "class_declaration": "class",
    "protocol_declaration": "protocol",
}

SYSTEM_FRAMEWORKS = {
    "Foundation", "UIKit", "SwiftUI", "Combine", "CoreData", "CoreGraphics",
    "AVFoundation", "MapKit", "WebKit", "XCTest", "Dispatch", "os",
}


def scan_file(swift_file: SwiftFile) -> list[ClassMetadata]:
    parser, _ = swift_parser()
    source = swift_file.content.encode("utf8")
    tree = parser.parse(source)
    root = tree.root_node

    imports = _collect_imports(root, source)
    file_path = Path(swift_file.path)

    results: list[ClassMetadata] = []
    init_param_names_by_type: dict[str, set[str]] = {}

    # First pass: top-level type declarations.
    for child in _iter_children(root):
        meta = _scan_decl(child, source, file_path, imports)
        if meta is not None:
            results.append(meta)
            init_param_names_by_type[meta.name] = _collect_init_param_names(meta)

    # Second pass: refine injection style now that we know which names appear
    # in init parameter lists for the same type (covers extensions).
    by_name: dict[str, list[ClassMetadata]] = {}
    for m in results:
        by_name.setdefault(m.extends or m.name, []).append(m)

    for name, group in by_name.items():
        init_params: set[str] = set()
        for m in group:
            init_params |= _collect_init_param_names(m)
        for m in group:
            for prop in m.properties:
                if prop.name in init_params and prop.injection_style == "none":
                    prop.injection_style = "init"

    # Populate dependencies from properties whose type looks like a user type.
    for m in results:
        m.dependencies = _build_dependencies(m, imports)

    return results


# ---------------------------------------------------------------------------
# Traversal helpers
# ---------------------------------------------------------------------------


def _iter_children(node) -> Iterable:
    for i in range(node.child_count):
        yield node.child(i)


def _named_children(node) -> Iterable:
    for i in range(node.named_child_count):
        yield node.named_child(i)


def _find_first(node, type_name: str):
    for c in _named_children(node):
        if c.type == type_name:
            return c
    return None


def _find_all(node, type_names: set[str]) -> list:
    matches = []
    for c in _named_children(node):
        if c.type in type_names:
            matches.append(c)
    return matches


def _walk(node):
    yield node
    for c in _named_children(node):
        yield from _walk(c)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _collect_imports(root, source: bytes) -> list[str]:
    out: list[str] = []
    for node in _walk(root):
        if node.type == "import_declaration":
            text = node_text(node, source).strip()
            # "import Foo" or "import class Foo.Bar"
            parts = text.split()
            if parts:
                out.append(parts[-1])
    return out


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def _decl_kind(node) -> Optional[tuple[KindLiteral, bool]]:
    """Return (kind, is_extension) for top-level type-declaration-like nodes."""
    if node.type == "class_declaration":
        # tree-sitter-swift uses class_declaration for class/struct/enum/actor/extension.
        first_kw = None
        for c in _iter_children(node):
            if c.type in {"class", "struct", "enum", "actor", "extension"}:
                first_kw = c.type
                break
        if first_kw == "extension":
            return ("extension", True)
        if first_kw in {"class", "struct", "enum", "actor"}:
            return (first_kw, False)  # type: ignore[return-value]
        return ("class", False)
    if node.type == "protocol_declaration":
        return ("protocol", False)
    return None


def _scan_decl(node, source: bytes, file_path: Path, imports: list[str]) -> Optional[ClassMetadata]:
    kind_info = _decl_kind(node)
    if kind_info is None:
        return None
    kind, is_extension = kind_info

    name = _extract_decl_name(node, source)
    if not name:
        return None

    superclass, protocols = _extract_inheritance(node, source)
    # Only class/actor declarations can have a real superclass. For
    # struct/enum/protocol/extension, every inherited type is a protocol.
    if kind not in {"class", "actor"} and superclass is not None:
        protocols = [superclass, *protocols]
        superclass = None
    elif kind in {"class", "actor"} and superclass is not None:
        # If the only inherited name clearly looks like a protocol, treat it as one.
        looks_protocol = (
            superclass.endswith("Protocol")
            or superclass.endswith("Delegate")
            or superclass in {"Codable", "Decodable", "Encodable", "Hashable",
                              "Equatable", "Comparable", "Error", "Sendable",
                              "CustomStringConvertible", "Identifiable"}
        )
        if looks_protocol:
            protocols = [superclass, *protocols]
            superclass = None

    methods: list[MethodMetadata] = []
    properties: list[PropertyMetadata] = []
    body = _find_decl_body(node)
    if body is not None:
        for member in _named_children(body):
            if member.type in {"function_declaration", "init_declaration", "deinit_declaration"}:
                methods.append(_extract_method(member, source))
            elif member.type == "property_declaration":
                properties.extend(_extract_properties(member, source))

    return ClassMetadata(
        name=name,
        kind=kind,
        file_path=file_path,
        superclass=superclass,
        protocols=protocols,
        properties=properties,
        methods=methods,
        imports=imports,
        is_extension=is_extension,
        extends=name if is_extension else None,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
    )


def _extract_decl_name(node, source: bytes) -> Optional[str]:
    for c in _named_children(node):
        if c.type in {"type_identifier", "user_type", "simple_identifier"}:
            return node_text(c, source).strip()
    # Fallback: scan unnamed children for an identifier-ish token.
    for c in _iter_children(node):
        if c.type in {"type_identifier", "simple_identifier"}:
            return node_text(c, source).strip()
    return None


def _find_decl_body(node):
    for c in _named_children(node):
        if c.type in {"class_body", "protocol_body", "enum_class_body", "declaration_block", "type_body"}:
            return c
    # Some grammars wrap body as last child with curly braces; fall back to scanning for first
    # child containing function_declaration or property_declaration.
    for c in _named_children(node):
        for sub in _named_children(c):
            if sub.type in {"function_declaration", "property_declaration", "init_declaration"}:
                return c
    return None


def _extract_inheritance(node, source: bytes) -> tuple[Optional[str], list[str]]:
    types: list[str] = []
    for c in _named_children(node):
        if c.type in {"inheritance_specifier", "type_inheritance_clause", "inheritance_clause"}:
            for t in _walk(c):
                if t.type in {"type_identifier", "user_type"}:
                    text = node_text(t, source).strip()
                    if text and text not in types:
                        types.append(text)
    if not types:
        return None, []
    # Heuristic: first entry is treated as superclass for classes; rest are protocols.
    # For protocols/structs/enums the caller may discard superclass.
    return types[0], types[1:]


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------


def _extract_method(node, source: bytes) -> MethodMetadata:
    name = _method_name(node, source)
    access, is_static, is_async, throws = _modifiers(node, source)
    params = _extract_parameters(node, source)
    return_type = _extract_return_type(node, source)
    body = _find_first(node, "function_body") or _find_first(node, "code_block")
    body_text = node_text(body, source) if body is not None else ""
    line_count = (node.end_point[0] - node.start_point[0]) + 1

    return MethodMetadata(
        name=name,
        parameters=params,
        return_type=return_type,
        access_level=access,
        is_static=is_static,
        is_async=is_async,
        throws=throws,
        line_count=line_count,
        body_text=body_text,
    )


def _method_name(node, source: bytes) -> str:
    if node.type == "init_declaration":
        return "init"
    if node.type == "deinit_declaration":
        return "deinit"
    for c in _named_children(node):
        if c.type in {"simple_identifier", "identifier"}:
            return node_text(c, source).strip()
    return ""


def _modifiers(node, source: bytes) -> tuple[AccessLevel, bool, bool, bool]:
    access: AccessLevel = "internal"
    is_static = False
    is_async = False
    throws = False
    mod_node = _find_first(node, "modifiers")
    if mod_node is not None:
        text = node_text(mod_node, source)
        tokens = text.split()
        for t in tokens:
            if t in ACCESS_KEYWORDS:
                access = t  # type: ignore[assignment]
            if t in {"static", "class"}:
                is_static = True
    # async / throws often appear as direct children of the function decl.
    for c in _iter_children(node):
        if c.type == "async":
            is_async = True
        if c.type == "throws":
            throws = True
    return access, is_static, is_async, throws


def _extract_parameters(node, source: bytes) -> list[ParameterMetadata]:
    out: list[ParameterMetadata] = []
    for c in _walk(node):
        if c.type == "parameter":
            label: Optional[str] = None
            name = ""
            type_text: Optional[str] = None
            default: Optional[str] = None
            idents = [k for k in _named_children(c) if k.type in {"simple_identifier", "identifier"}]
            if len(idents) >= 2:
                label = node_text(idents[0], source)
                name = node_text(idents[1], source)
            elif idents:
                # Swift: a single identifier means label == internal name.
                name = node_text(idents[0], source)
                label = name
            # Wildcard external label `_`.
            for k in _named_children(c):
                if k.type == "wildcard_pattern" or (k.type == "simple_identifier" and node_text(k, source) == "_"):
                    label = "_"
                    break
            # type
            for k in _named_children(c):
                if "type" in k.type and k.type != "type_identifier":
                    type_text = node_text(k, source).strip()
                    break
            if type_text is None:
                t = _find_first(c, "type_identifier") or _find_first(c, "user_type")
                if t is not None:
                    type_text = node_text(t, source).strip()
            for k in _named_children(c):
                if k.type in {"default_value", "expression"}:
                    default = node_text(k, source).strip()
                    break
            if name:
                out.append(ParameterMetadata(label=label, name=name, type=type_text, default_value=default))
        elif c.type == "function_signature":
            break  # parameters live inside the signature; outer walk already handles it
    # Deduplicate while preserving order.
    seen: set[tuple] = set()
    deduped: list[ParameterMetadata] = []
    for p in out:
        key = (p.label, p.name, p.type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


_TYPE_NODE_NAMES = {
    "user_type", "optional_type", "tuple_type", "function_type",
    "array_type", "dictionary_type", "metatype", "opaque_type",
    "existential_type", "protocol_composition_type", "type_identifier",
}


def _extract_return_type(node, source: bytes) -> Optional[str]:
    # Look at direct named children only, skipping nodes that belong to the
    # parameter list / function body / modifiers. The return type, when present,
    # sits as a top-level type-ish child between the parameters and the body.
    skip = {
        "simple_identifier", "identifier", "parameter", "function_body",
        "code_block", "modifiers", "where_clause", "type_parameters",
        "async", "throws", "rethrows",
    }
    for c in _named_children(node):
        if c.type in skip:
            continue
        if c.type in _TYPE_NODE_NAMES:
            return node_text(c, source).strip()
    return None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def _extract_properties(node, source: bytes) -> list[PropertyMetadata]:
    access, is_static, _, _ = _modifiers(node, source)
    is_let = False
    has_default = False
    is_computed = False

    text = node_text(node, source)
    # Detect let vs var by inspecting the binding-pattern token text.
    binding = _find_first(node, "value_binding_pattern")
    if binding is not None:
        kw = node_text(binding, source).strip()
        if kw.startswith("let"):
            is_let = True
        elif kw.startswith("var"):
            is_let = False
    else:
        stripped = text.lstrip()
        if stripped.startswith("let "):
            is_let = True
    if "=" in text.split("{", 1)[0]:
        has_default = True
    if "{" in text:
        is_computed = True

    name = None
    for c in _walk(node):
        if c.type == "pattern":
            for k in _named_children(c):
                if k.type in {"simple_identifier", "identifier"}:
                    name = node_text(k, source).strip()
                    break
            if name:
                break
    if name is None:
        for c in _named_children(node):
            if c.type in {"simple_identifier", "identifier"}:
                name = node_text(c, source).strip()
                break
    if not name:
        return []

    type_text: Optional[str] = None
    # The type annotation, when present, is a direct child of property_declaration.
    # Walking into `modifiers` would incorrectly pick up @attribute types like @IBOutlet.
    for c in _named_children(node):
        if c.type == "type_annotation":
            t = node_text(c, source).strip().lstrip(":").strip()
            type_text = t
            break

    is_optional = bool(type_text and (type_text.endswith("?") or type_text.endswith("!")))
    injection: InjectionStyle = "none"
    if not is_let and not is_computed and not has_default:
        injection = "property"
    # init-injection is filled in by a second pass once we know init params.

    return [
        PropertyMetadata(
            name=name,
            type=type_text,
            is_optional=is_optional,
            is_let=is_let,
            access_level=access,
            is_static=is_static,
            injection_style=injection,
            has_default=has_default,
            is_computed=is_computed,
        )
    ]


# ---------------------------------------------------------------------------
# Dependencies / init-param detection
# ---------------------------------------------------------------------------


def _collect_init_param_names(meta: ClassMetadata) -> set[str]:
    names: set[str] = set()
    for m in meta.methods:
        if m.name == "init":
            for p in m.parameters:
                names.add(p.name)
    return names


def _build_dependencies(meta: ClassMetadata, imports: list[str]) -> list[DependencyInfo]:
    deps: list[DependencyInfo] = []
    for prop in meta.properties:
        if not prop.type:
            continue
        base = prop.type.strip()
        for trim in ("?", "!"):
            if base.endswith(trim):
                base = base[:-1]
        base = base.split("<", 1)[0].strip()
        if not base or base[0].islower():
            continue
        if base in {"String", "Int", "Double", "Float", "Bool", "Date", "URL", "Data", "Decimal"}:
            continue
        deps.append(
            DependencyInfo(
                type_name=base,
                injection_style=prop.injection_style,
                is_protocol=base.endswith("Protocol") or base.endswith("Delegate"),
                is_singleton=False,
                is_system_framework=base in SYSTEM_FRAMEWORKS or base.startswith(("UI", "NS")),
            )
        )
    return deps
