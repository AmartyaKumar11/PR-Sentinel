"""Tier 2: Tree-sitter tags plus name matching. One file for every non-Python language.

Edges over-estimate. An ambiguous call name links to every matching definition.
"""

from __future__ import annotations

from app.services.ast_parser import GraphEdge, GraphNode
from app.services.parsers.base import LanguageParser, module_name, node_key, node_text, walk

# The only per-language table. Python is listed so the file filter accepts .py;
# graph_builder sends .py to PythonParser, not here.
EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "tsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".lua": "lua",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
    ".r": "r",
    ".jl": "julia",
}

_IDENTS = {"identifier", "property_identifier", "field_identifier", "type_identifier"}
_FUNC = (
    "function_declaration",
    "function_definition",
    "function_item",
    "method_declaration",
    "method_definition",
    "constructor_declaration",
    "arrow_function",
    "function_expression",
)
_CLASS = (
    "class_declaration",
    "class_definition",
    "class_specifier",
    "interface_declaration",
    "struct_item",
)
_CALL = ("call_expression", "method_invocation", "invocation_expression")

_parsers: dict = {}
_queries: dict = {}


def _ext(file_path: str) -> str:
    return "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""


def _kind(node_type: str) -> str | None:
    if any(bit in node_type for bit in _FUNC):
        return "function"
    if any(bit in node_type for bit in _CLASS):
        return "class"
    if any(bit in node_type for bit in _CALL) or node_type == "call":
        return "call"
    return None


def _parser(grammar: str):
    if grammar not in _parsers:
        from tree_sitter_language_pack import get_parser

        _parsers[grammar] = get_parser(grammar)
    return _parsers[grammar]


def _tags_query(grammar: str):
    if grammar in _queries:
        return _queries[grammar]
    query = None
    try:
        from tree_sitter import Query
        from tree_sitter_language_pack import get_language, get_tags_query

        text = get_tags_query(grammar)
        if text:
            query = Query(get_language(grammar), text)
    except Exception:
        query = None
    _queries[grammar] = query
    return query


def _symbol(node, src: bytes) -> str | None:
    if node is None:
        return None
    if node.type in _IDENTS:
        return node_text(node, src)
    left = (
        node.child_by_field_name("object")
        or node.child_by_field_name("operand")
        or node.child_by_field_name("path")
    )
    right = (
        node.child_by_field_name("property")
        or node.child_by_field_name("field")
        or node.child_by_field_name("name")
    )
    if left is not None and right is not None and node_key(left) != node_key(right):
        head, tail = _symbol(left, src), _symbol(right, src)
        if head and tail:
            return f"{head}.{tail}"
    name = node.child_by_field_name("name")
    if name is not None and name.type in _IDENTS:
        return node_text(name, src)
    return None


def _callee(node, src: bytes) -> str | None:
    function = node.child_by_field_name("function")
    if function is not None:
        return _symbol(function, src)
    return _symbol(node, src)


def _enclosing(node, ids: dict, kind: str) -> str | None:
    parent = node.parent
    while parent is not None:
        if _kind(parent.type) == kind:
            found = ids.get(node_key(parent))
            if found:
                return found
        parent = parent.parent
    return None


class UniversalParser(LanguageParser):
    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def parse_file(self, file_path: str, source_code: str):
        grammar = EXTENSION_MAP.get(_ext(file_path))
        if not grammar or grammar == "python":
            return [], []
        try:
            parser = _parser(grammar)
        except Exception:
            return [], []
        src = source_code.lstrip("\ufeff").encode("utf-8")
        tree = parser.parse(src)
        if tree.root_node.has_error:
            return [], []

        module = module_name(file_path)
        nodes: dict[str, GraphNode] = {}
        ids: dict[tuple, str] = {}

        def add_def(anchor, name: str, kind: str) -> None:
            if not name:
                return
            owner = _enclosing(anchor, ids, "class") if kind == "function" else None
            qid = f"{owner}.{name}" if owner else (f"{module}.{name}" if module else name)
            ids[node_key(anchor)] = qid
            nodes.setdefault(
                qid,
                GraphNode(
                    id=qid,
                    file=file_path,
                    type=kind,
                    line=anchor.start_point[0] + 1,
                    name=name,
                ),
            )

        def add_call(anchor, callee: str | None) -> None:
            if not callee:
                return
            source = _enclosing(anchor, ids, "function")
            if source:
                self.calls.append((source, callee, file_path))

        for node in walk(tree.root_node):
            kind = _kind(node.type)
            if kind in ("function", "class"):
                add_def(node, _symbol(node, src) or "", kind)
            elif node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value = node.child_by_field_name("value")
                if (
                    name_node is not None
                    and value is not None
                    and name_node.type in _IDENTS
                    and _kind(value.type) == "function"
                ):
                    add_def(value, node_text(name_node, src), "function")
        self._from_tags(grammar, tree.root_node, src, add_def, add_call)
        for node in walk(tree.root_node):
            if _kind(node.type) == "call":
                add_call(node, _callee(node, src))
        return list(nodes.values()), []

    def _from_tags(self, grammar, root, src, add_def, add_call) -> None:
        query = _tags_query(grammar)
        if query is None:
            return
        try:
            from tree_sitter import QueryCursor

            matches = QueryCursor(query).matches(root)
        except Exception:
            return
        for _index, captures in matches:
            name = None
            def_node = None
            def_kind = None
            call_node = None
            for cap, found in captures.items():
                if not found:
                    continue
                if cap == "name":
                    name = node_text(found[0], src)
                elif cap.startswith("definition.function") or cap.startswith("definition.method"):
                    def_node, def_kind = found[0], "function"
                elif cap.startswith("definition.class"):
                    def_node, def_kind = found[0], "class"
                elif cap.startswith("reference.call"):
                    call_node = found[0]
            if def_node is not None and name:
                add_def(def_node, name, def_kind)
            if call_node is not None:
                add_call(call_node, _callee(call_node, src) or name)

    def resolve(self, nodes: list[GraphNode]) -> list[GraphEdge]:
        by_id = {}
        bare: dict[str, list[str]] = {}
        for node in nodes:
            by_id.setdefault(node.id, node)
            bare.setdefault(node.name, []).append(node.id)
            tail = node.id.rsplit(".", 1)[-1]
            if tail != node.name:
                bare.setdefault(tail, []).append(node.id)
        edges = []
        seen = set()
        for source, callee, caller_file in self.calls:
            for target in _targets(callee, by_id, bare, caller_file):
                if target == source or target not in by_id:
                    continue
                key = (source, target)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(GraphEdge(source=source, target=target, type="calls"))
        return edges


def _targets(callee: str, by_id: dict, bare: dict, caller_file: str) -> list[str]:
    if callee in by_id:
        return [callee]
    if "." in callee:
        suffix = [qid for qid in by_id if qid.endswith("." + callee)]
        if len(suffix) == 1:
            return suffix
        if len(suffix) > 1:
            return _narrow(suffix, caller_file, by_id)
    cands = list(dict.fromkeys(bare.get(callee.rsplit(".", 1)[-1], ())))
    if len(cands) <= 1:
        return cands
    return _narrow(cands, caller_file, by_id)


def _narrow(cands: list[str], caller_file: str, by_id: dict) -> list[str]:
    """Same directory wins only when that leaves a single definition."""
    folder = caller_file.replace("\\", "/").rsplit("/", 1)[0] if "/" in caller_file else ""
    near = [
        qid
        for qid in cands
        if (by_id[qid].file.replace("\\", "/").rsplit("/", 1)[0] if "/" in by_id[qid].file else "") == folder
    ]
    if len(near) == 1:
        return near
    return cands
