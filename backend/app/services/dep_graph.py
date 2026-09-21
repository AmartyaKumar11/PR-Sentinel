"""Python AST → dependency graph (M-04)."""

from __future__ import annotations

import ast
from pathlib import Path


def build_dependency_graph_from_files(files: dict[str, str]) -> dict:
    """files: { "src/auth.py": source } → { nodes, edges }."""
    nodes: dict[str, dict] = {}
    # path → { local_name → qualified_id }
    defs: dict[str, dict[str, str]] = {}

    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        mod = _mod(path)
        defs[path] = {}
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qid = f"{mod}.{node.name}"
                nodes[qid] = {
                    "id": qid,
                    "name": node.name,
                    "file": path,
                    "type": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": node.lineno,
                }
                defs[path][node.name] = qid

    edges: list[dict] = []
    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        mod = _mod(path)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        # name → fully-qualified symbol we can match in nodes
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    local = a.asname or a.name
                    # from src.auth import validate_token
                    aliases[local] = f"{node.module}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    local = a.asname or a.name.split(".")[0]
                    aliases[local] = a.name  # module path e.g. src.auth

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller = f"{mod}.{node.name}"
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                callee = _callee(child.func, aliases, defs.get(path, {}), mod)
                if callee and callee in nodes and callee != caller:
                    edges.append({"from": caller, "to": callee, "type": "calls"})

    # Deduplicate
    seen: set[tuple] = set()
    uniq = []
    for e in edges:
        k = (e["from"], e["to"], e["type"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)

    return {"nodes": list(nodes.values()), "edges": uniq}


def build_dependency_graph_from_dir(root: Path, path_filter: str = "src/") -> dict:
    files: dict[str, str] = {}
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if path_filter and not rel.startswith(path_filter):
            continue
        files[rel] = p.read_text(encoding="utf-8")
    return build_dependency_graph_from_files(files)


def _mod(path: str) -> str:
    return path.replace("\\", "/").removesuffix(".py").replace("/", ".")


def _callee(func, aliases: dict[str, str], local_defs: dict[str, str], mod: str) -> str | None:
    if isinstance(func, ast.Name):
        if func.id in local_defs:
            return local_defs[func.id]
        if func.id in aliases:
            return aliases[func.id]
        return f"{mod}.{func.id}"

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base = func.value.id
        if base in aliases:
            # from src.auth import validate_token as vt → already full
            # import src.auth → aliases['src'] or aliases['auth']?
            # from src.auth import validate_token → Name, not Attribute
            # from src import auth → auth.validate_token
            return f"{aliases[base]}.{func.attr}"
    return None
