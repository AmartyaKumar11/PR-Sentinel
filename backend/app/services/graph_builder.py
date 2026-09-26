"""Pick a parser from the file extension and merge one dependency graph."""

from __future__ import annotations

from app.services.parsers.base import LanguageParser
from app.services.parsers.python_parser import PythonParser
from app.services.parsers.universal_parser import EXTENSION_MAP, UniversalParser

SUPPORTED_EXTENSIONS = set(EXTENSION_MAP)


def _ext(file_path: str) -> str:
    return "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""


def is_supported_source(path: str) -> bool:
    return _ext(path) in SUPPORTED_EXTENSIONS


def get_parser(file_path: str) -> LanguageParser | None:
    ext = _ext(file_path)
    if ext == ".py":
        return PythonParser()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    return UniversalParser()


def build_graph(file_contents: dict[str, str]) -> dict:
    """Python stays on the ast parser. Every other language is name-matched afterward."""
    universal = UniversalParser()
    all_nodes = []
    all_edges = []

    for filepath, source in file_contents.items():
        ext = _ext(filepath)
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        parser = PythonParser() if ext == ".py" else universal
        try:
            nodes, edges = parser.parse_file(filepath, source)
        except Exception:
            continue
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    all_edges.extend(universal.resolve(all_nodes))

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
