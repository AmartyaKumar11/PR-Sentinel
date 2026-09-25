"""Pick a parser from the file extension and merge one dependency graph."""

from __future__ import annotations

from app.services.parsers.base import LanguageParser
from app.services.parsers.go_parser import GoParser
from app.services.parsers.java_parser import JavaParser
from app.services.parsers.javascript_parser import JavaScriptParser
from app.services.parsers.python_parser import PythonParser
from app.services.parsers.typescript_parser import TypeScriptParser

SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java"}

PARSER_MAP: dict[str, LanguageParser] = {
    ".py": PythonParser(),
    ".ts": TypeScriptParser(),
    ".tsx": TypeScriptParser(),
    ".js": JavaScriptParser(),
    ".jsx": JavaScriptParser(),
    ".go": GoParser(),
    ".java": JavaParser(),
}


def is_supported_source(path: str) -> bool:
    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    return ext in SUPPORTED_EXTENSIONS


def get_parser(file_path: str) -> LanguageParser | None:
    ext = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    return PARSER_MAP.get(ext)


def build_graph(file_contents: dict[str, str]) -> dict:
    """Build a dependency graph. Language comes from the file extension."""
    all_nodes = []
    all_edges = []

    for filepath, source in file_contents.items():
        parser = get_parser(filepath)
        if parser is None:
            continue
        try:
            nodes, edges = parser.parse_file(filepath, source)
        except Exception:
            continue
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    node_ids = {node.id for node in all_nodes}
    seen: set[tuple[str, str, str]] = set()
    valid_edges = []
    for edge in all_edges:
        if edge.target not in node_ids:
            continue
        key = (edge.source, edge.target, edge.type)
        if key in seen:
            continue
        seen.add(key)
        valid_edges.append(edge)

    unique_nodes = {}
    for node in all_nodes:
        unique_nodes.setdefault(node.id, node)

    return {
        "nodes": [
            {"id": node.id, "file": node.file, "type": node.type, "line": node.line, "name": node.name}
            for node in unique_nodes.values()
        ],
        "edges": [{"from": edge.source, "to": edge.target, "type": edge.type} for edge in valid_edges],
    }
