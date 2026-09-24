"""Python AST → dependency graph (BUILD-SEQUENCE Phase 1.3)."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GraphNode:
    id: str
    file: str
    type: str
    line: int
    name: str


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str


def _module_name(file_path: str) -> str:
    """src/auth.py → src.auth (keeps package prefix for demo imports)."""
    return file_path.replace("\\", "/").removesuffix(".py").replace("/", ".")


def parse_file(file_path: str, source_code: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    mod = _module_name(file_path)
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return nodes, edges

    local_defs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qid = f"{mod}.{node.name}"
            nodes.append(
                GraphNode(id=qid, file=file_path, type="function", line=node.lineno, name=node.name)
            )
            local_defs[node.name] = qid
        elif isinstance(node, ast.ClassDef):
            qid = f"{mod}.{node.name}"
            nodes.append(
                GraphNode(id=qid, file=file_path, type="class", line=node.lineno, name=node.name)
            )
            local_defs[node.name] = qid

    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                local = a.asname or a.name
                aliases[local] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                aliases[local] = a.name

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        caller = f"{mod}.{node.name}"
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            callee = _resolve_call(child.func, aliases, local_defs, mod)
            if callee and callee != caller:
                edges.append(GraphEdge(source=caller, target=callee, type="calls"))

    return nodes, edges


def build_graph(file_contents: dict[str, str]) -> dict:
    all_nodes: dict[str, GraphNode] = {}
    all_edges: list[GraphEdge] = []
    for path, source in file_contents.items():
        if not path.endswith(".py"):
            continue
        nodes, edges = parse_file(path, source)
        for n in nodes:
            all_nodes[n.id] = n
        all_edges.extend(edges)

    node_ids = set(all_nodes)
    edges = [
        {"from": e.source, "to": e.target, "type": e.type}
        for e in all_edges
        if e.target in node_ids
    ]
    # dedupe
    seen = set()
    uniq = []
    for e in edges:
        k = (e["from"], e["to"], e["type"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)

    return {
        "nodes": [
            {"id": n.id, "file": n.file, "type": n.type, "line": n.line, "name": n.name}
            for n in all_nodes.values()
        ],
        "edges": uniq,
    }


def get_subgraph(full_graph: dict, relevant_files: list[str], max_depth: int = 3) -> dict:
    seeds = {n["id"] for n in full_graph.get("nodes", []) if n.get("file") in relevant_files}
    adj: dict[str, set[str]] = {}
    for e in full_graph.get("edges", []):
        adj.setdefault(e["from"], set()).add(e["to"])
        adj.setdefault(e["to"], set()).add(e["from"])

    keep: set[str] = set()
    q: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
    while q:
        nid, d = q.popleft()
        if nid in keep:
            continue
        keep.add(nid)
        if d >= max_depth:
            continue
        for nb in adj.get(nid, ()):
            if nb not in keep:
                q.append((nb, d + 1))

    return {
        "nodes": [n for n in full_graph.get("nodes", []) if n["id"] in keep],
        "edges": [
            e
            for e in full_graph.get("edges", [])
            if e["from"] in keep and e["to"] in keep
        ],
    }


def _resolve_call(func, aliases: dict[str, str], local_defs: dict[str, str], mod: str) -> str | None:
    if isinstance(func, ast.Name):
        if func.id in local_defs:
            return local_defs[func.id]
        if func.id in aliases:
            return aliases[func.id]
        return f"{mod}.{func.id}"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base = func.value.id
        if base in aliases:
            return f"{aliases[base]}.{func.attr}"
    return None


# Back-compat for older imports
def build_dependency_graph_from_files(files: dict[str, str]) -> dict:
    return build_graph(files)


def build_dependency_graph_from_dir(root: Path, path_filter: str = "src/") -> dict:
    files: dict[str, str] = {}
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if path_filter and not rel.startswith(path_filter):
            continue
        files[rel] = p.read_text(encoding="utf-8")
    return build_graph(files)
