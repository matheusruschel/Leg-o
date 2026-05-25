"""Objective-C AST extraction via tree-sitter.

Focused on extracting class interfaces and the rough shape of their methods
and properties — enough for Layer 2 (Claude) to reason about testability.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from ..models import (
    AccessLevel,
    ClassMetadata,
    MethodMetadata,
    ParameterMetadata,
    PropertyMetadata,
    SwiftFile,
)
from ._ts import node_text, objc_parser


def _named_children(node) -> Iterable:
    for i in range(node.named_child_count):
        yield node.named_child(i)


def _walk(node):
    yield node
    for c in _named_children(node):
        yield from _walk(c)


def scan_file(swift_file: SwiftFile) -> list[ClassMetadata]:
    parser, _ = objc_parser()
    source = swift_file.content.encode("utf8")
    tree = parser.parse(source)
    root = tree.root_node

    imports = _collect_imports(root, source)
    results: list[ClassMetadata] = []
    file_path = Path(swift_file.path)

    for node in _walk(root):
        if node.type in {"class_interface", "class_implementation"}:
            meta = _scan_interface(node, source, file_path, imports)
            if meta is not None:
                results.append(meta)
    return results


def _collect_imports(root, source: bytes) -> list[str]:
    out: list[str] = []
    for node in _walk(root):
        if node.type in {"preproc_import", "preproc_include", "module_import"}:
            text = node_text(node, source).strip()
            out.append(text)
    return out


def _scan_interface(node, source: bytes, file_path: Path, imports: list[str]) -> Optional[ClassMetadata]:
    name = None
    superclass: Optional[str] = None
    protocols: list[str] = []

    for c in _named_children(node):
        if c.type in {"identifier", "class_name"} and name is None:
            name = node_text(c, source).strip()
        elif c.type == "superclass_reference":
            superclass = node_text(c, source).strip().lstrip(":").strip()
        elif c.type == "protocol_reference_list":
            for t in _walk(c):
                if t.type == "identifier":
                    protocols.append(node_text(t, source).strip())

    if not name:
        return None

    methods: list[MethodMetadata] = []
    properties: list[PropertyMetadata] = []

    for n in _walk(node):
        if n.type in {"method_declaration", "method_definition"}:
            methods.append(_extract_method(n, source))
        elif n.type == "property_declaration":
            p = _extract_property(n, source)
            if p is not None:
                properties.append(p)

    return ClassMetadata(
        name=name,
        kind="class",
        file_path=file_path,
        superclass=superclass,
        protocols=protocols,
        properties=properties,
        methods=methods,
        imports=imports,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
    )


def _extract_method(node, source: bytes) -> MethodMetadata:
    text = node_text(node, source).strip()
    head = text.split("{", 1)[0].strip().rstrip(";")
    is_static = head.startswith("+")
    # Selector pieces: parse params from grammar nodes.
    params: list[ParameterMetadata] = []
    name_parts: list[str] = []

    for c in _walk(node):
        if c.type == "keyword_selector":
            for sub in _named_children(c):
                if sub.type == "keyword_declarator":
                    label = None
                    name = ""
                    type_text = None
                    for k in _named_children(sub):
                        if k.type == "identifier":
                            if label is None:
                                label = node_text(k, source)
                                name_parts.append(label)
                            else:
                                name = node_text(k, source)
                        elif "type" in k.type:
                            type_text = node_text(k, source).strip().strip("()")
                    if label is not None:
                        params.append(ParameterMetadata(label=label, name=name or label, type=type_text))
        elif c.type == "unary_selector":
            for sub in _named_children(c):
                if sub.type == "identifier":
                    name_parts.append(node_text(sub, source))

    method_name = ":".join(name_parts) + (":" if params else "")
    return_type: Optional[str] = None
    for c in _named_children(node):
        if "type" in c.type:
            return_type = node_text(c, source).strip().strip("()")
            break

    line_count = (node.end_point[0] - node.start_point[0]) + 1
    return MethodMetadata(
        name=method_name or "<unnamed>",
        parameters=params,
        return_type=return_type,
        access_level="internal",
        is_static=is_static,
        line_count=line_count,
        body_text="",
    )


def _extract_property(node, source: bytes) -> Optional[PropertyMetadata]:
    name: Optional[str] = None
    type_text: Optional[str] = None
    for c in _walk(node):
        if c.type == "identifier" and name is None:
            # Last identifier in the property decl is the property name; keep updating.
            name = node_text(c, source).strip()
        elif "type" in c.type and type_text is None:
            type_text = node_text(c, source).strip()
    if not name:
        return None
    access: AccessLevel = "internal"
    return PropertyMetadata(
        name=name,
        type=type_text,
        access_level=access,
        injection_style="property",
    )
