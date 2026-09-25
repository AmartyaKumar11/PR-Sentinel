"""Java dependency graph via Tree-sitter."""

from __future__ import annotations

from app.services.ast_parser import GraphEdge, GraphNode
from app.services.parsers.base import LanguageParser, module_name, node_key, node_text, walk


def _parser():
    from tree_sitter_language_pack import get_parser

    return get_parser("java")


class JavaParser(LanguageParser):
    def _package_module(self, file_path: str) -> str:
        path = file_path.replace("\\", "/")
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if not directory:
            return ""
        return module_name(directory + "/x").rsplit(".", 1)[0]

    def parse_file(self, file_path: str, source_code: str):
        src = source_code.encode("utf-8")
        tree = _parser().parse(src)
        if tree.root_node.has_error:
            return [], []

        package = self._package_module(file_path)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        imports: dict[str, str] = {}
        vartypes: dict[str, str] = {}
        fn_ids: dict[tuple[int, int, str], str] = {}
        class_ids: dict[tuple[int, int, str], str] = {}

        for node in walk(tree.root_node):
            if node.type in ("class_declaration", "interface_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = node_text(name_node, src)
                qid = f"{package}.{name}" if package else name
                nodes.append(GraphNode(id=qid, file=file_path, type="class", line=node.start_point[0] + 1, name=name))
                class_ids[node_key(node)] = qid
            elif node.type in ("method_declaration", "constructor_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    continue
                name = node_text(name_node, src)
                class_id = _enclosing_class(node, class_ids)
                qid = f"{class_id}.{name}" if class_id else name
                nodes.append(GraphNode(id=qid, file=file_path, type="function", line=node.start_point[0] + 1, name=name))
                fn_ids[node_key(node)] = qid
            elif node.type == "import_declaration":
                _read_import(node, src, package, edges, imports)
            elif node.type in ("local_variable_declaration", "field_declaration"):
                _read_var(node, src, vartypes)

        for node in walk(tree.root_node):
            if node.type != "method_invocation":
                continue
            owner = _owner(node, fn_ids)
            if not owner:
                continue
            target = _java_target(node, src, imports, vartypes)
            if target and target != owner:
                edges.append(GraphEdge(source=owner, target=target, type="calls"))
        return nodes, edges


def _read_import(node, src: bytes, package: str, edges: list[GraphEdge], imports: dict[str, str]) -> None:
    scoped = next((child for child in node.children if child.type in ("scoped_identifier", "identifier")), None)
    if scoped is None:
        return
    parts = node_text(scoped, src).split(".")
    if any(child.type == "asterisk" for child in node.children):
        target = parts[-1] if parts else ""
        if target:
            edges.append(GraphEdge(source=package, target=target, type="imports"))
        return
    target = ".".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    imports[parts[-1]] = target
    edges.append(GraphEdge(source=package, target=target, type="imports"))


def _read_var(node, src: bytes, vartypes: dict[str, str]) -> None:
    type_node = next((child for child in node.children if child.type == "type_identifier"), None)
    if type_node is None:
        return
    type_name = node_text(type_node, src)
    for child in walk(node):
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        if name_node is not None:
            vartypes[node_text(name_node, src)] = type_name


def _enclosing_class(node, class_ids: dict[tuple[int, int, str], str]) -> str:
    current = node.parent
    while current is not None:
        found = class_ids.get(node_key(current))
        if found:
            return found
        current = current.parent
    return ""


def _owner(node, fn_ids: dict[tuple[int, int, str], str]) -> str | None:
    current = node.parent
    while current is not None:
        found = fn_ids.get(node_key(current))
        if found:
            return found
        current = current.parent
    return None


def _java_target(node, src: bytes, imports: dict[str, str], vartypes: dict[str, str]) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    method = node_text(name_node, src)
    obj = node.child_by_field_name("object")
    if obj is None:
        return method
    obj_name = node_text(obj, src).split(".")[-1]
    if obj_name in imports:
        return f"{imports[obj_name]}.{method}"
    type_name = vartypes.get(obj_name)
    if type_name:
        return f"{imports.get(type_name, type_name)}.{method}"
    return f"{obj_name}.{method}"
