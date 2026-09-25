"""Shared graph types and module-name rules for every language parser."""

from __future__ import annotations

from app.services.ast_parser import GraphEdge, GraphNode

__all__ = ["GraphEdge", "GraphNode", "LanguageParser", "module_name", "node_text", "walk"]

# ponytail: prefix list is the common roots we actually see, including Java `com/`
_PREFIXES = ("src/", "lib/", "pkg/", "app/", "internal/", "cmd/", "com/")


def module_name(file_path: str) -> str:
    """src/auth.py → auth. src/utils/helpers.ts → utils.helpers."""
    path = file_path.replace("\\", "/")
    leaf = path.rsplit("/", 1)[-1]
    if "." in leaf:
        path = path[: len(path) - len(leaf) + leaf.rfind(".")]
    while True:
        stripped = False
        for prefix in _PREFIXES:
            if path.startswith(prefix):
                path = path[len(prefix) :]
                stripped = True
                break
        if not stripped:
            break
    return path.replace("/", ".")


def node_key(node) -> tuple[int, int, str]:
    """Tree-sitter returns a fresh wrapper on each access, so id() is useless."""
    return (node.start_byte, node.end_byte, node.type)


def node_text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


class LanguageParser:
    """Abstract base for all language parsers."""

    def parse_file(self, file_path: str, source_code: str) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Parse a single file and return nodes + edges."""
        raise NotImplementedError

    def _module_name(self, file_path: str) -> str:
        """Convert file path to module identifier.

        src/auth.py → auth
        src/utils/helpers.ts → utils.helpers
        pkg/handlers/auth.go → handlers.auth
        com/example/Auth.java → example.Auth
        """
        return module_name(file_path)


def resolve_module_spec(spec: str, file_path: str) -> str | None:
    """Relative or @/ import → module id. Bare packages return None."""
    if spec.startswith("@/"):
        return spec[2:].replace("/", ".")
    if not spec.startswith("."):
        return None
    parent = file_path.replace("\\", "/")
    parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    parts = [part for part in parent.split("/") if part]
    for piece in spec.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if parts:
                parts.pop()
            continue
        parts.append(piece)
    return module_name("/".join(parts))


def string_value(node, src: bytes) -> str:
    for child in node.children:
        if child.type in ("string_fragment", "interpreted_string_literal_content"):
            return node_text(child, src)
    return node_text(node, src).strip("'\"`")
