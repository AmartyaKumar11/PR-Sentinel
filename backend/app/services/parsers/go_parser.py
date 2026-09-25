"""Go dependency graph via Tree-sitter. Module id is the directory, not the file."""

from __future__ import annotations

from app.services.ast_parser import GraphEdge, GraphNode
from app.services.parsers.base import LanguageParser, module_name, node_key, node_text, string_value, walk


def _parser():
    from tree_sitter_language_pack import get_parser

    return get_parser("go")


class GoParser(LanguageParser):
    def _package_module(self, file_path: str) -> str:
        path = file_path.replace("\\", "/")
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if not directory:
            return "main"
        return module_name(directory + "/x").rsplit(".", 1)[0] or "main"

    def parse_file(self, file_path: str, source_code: str):
        src = source_code.encode("utf-8")
        tree = _parser().parse(src)
        if tree.root_node.has_error:
            return [], []

        module = self._package_module(file_path)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        aliases: dict[str, str] = {}
        fn_ids: dict[tuple[int, int, str], str] = {}

        for node in walk(tree.root_node):
            if node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = node_text(name_node, src)
                qid = f"{module}.{name}"
                nodes.append(GraphNode(id=qid, file=file_path, type="function", line=node.start_point[0] + 1, name=name))
                fn_ids[node_key(node)] = qid
            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = node_text(name_node, src)
                receiver = _receiver_type(node, src)
                qid = f"{module}.{receiver}.{name}" if receiver else f"{module}.{name}"
                nodes.append(GraphNode(id=qid, file=file_path, type="function", line=node.start_point[0] + 1, name=name))
                fn_ids[node_key(node)] = qid
            elif node.type == "type_spec":
                type_node = node.child_by_field_name("type")
                name_node = node.child_by_field_name("name")
                if type_node is None or name_node is None or type_node.type not in ("struct_type", "interface_type"):
                    continue
                name = node_text(name_node, src)
                qid = f"{module}.{name}"
                nodes.append(GraphNode(id=qid, file=file_path, type="class", line=node.start_point[0] + 1, name=name))
            elif node.type == "import_spec":
                path_node = node.child_by_field_name("path")
                if path_node is None:
                    path_node = next((c for c in node.children if c.type == "interpreted_string_literal"), None)
                if path_node is None:
                    continue
                imported = string_value(path_node, src)
                if "/" not in imported:
                    continue
                target = imported.rsplit("/", 1)[-1]
                alias_node = node.child_by_field_name("name")
                alias = node_text(alias_node, src) if alias_node is not None else target
                aliases[alias] = target
                edges.append(GraphEdge(source=module, target=target, type="imports"))

        for node in walk(tree.root_node):
            if node.type != "call_expression":
                continue
            owner = _owner(node, fn_ids)
            callee = node.child_by_field_name("function")
            if not owner or callee is None:
                continue
            target = _go_target(callee, src, module, aliases)
            if target and target != owner:
                edges.append(GraphEdge(source=owner, target=target, type="calls"))
        return nodes, edges


def _receiver_type(node, src: bytes) -> str:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return ""
    for child in walk(receiver):
        if child.type == "type_identifier":
            return node_text(child, src)
    return ""


def _owner(node, fn_ids: dict[tuple[int, int, str], str]) -> str | None:
    current = node.parent
    while current is not None:
        found = fn_ids.get(node_key(current))
        if found:
            return found
        current = current.parent
    return None


def _go_target(callee, src: bytes, module: str, aliases: dict[str, str]) -> str | None:
    if callee.type == "identifier":
        name = node_text(callee, src)
        return f"{module}.{name}"
    if callee.type != "selector_expression":
        return None
    operand = callee.child_by_field_name("operand")
    field = callee.child_by_field_name("field")
    if operand is None or field is None or operand.type != "identifier":
        return None
    pkg = node_text(operand, src)
    name = node_text(field, src)
    if pkg not in aliases:
        return None
    return f"{aliases[pkg]}.{name}"
