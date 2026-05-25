"""tree-sitter parser singletons. Imported lazily to keep import-time cheap."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def swift_parser():
    from tree_sitter import Language, Parser  # type: ignore
    import tree_sitter_swift  # type: ignore

    lang = Language(tree_sitter_swift.language())
    return Parser(lang), lang


@lru_cache(maxsize=1)
def objc_parser():
    from tree_sitter import Language, Parser  # type: ignore
    import tree_sitter_objc  # type: ignore

    lang = Language(tree_sitter_objc.language())
    return Parser(lang), lang


def node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf8", errors="replace")
