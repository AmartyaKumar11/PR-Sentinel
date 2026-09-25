"""TypeScript / TSX dependency graph via Tree-sitter."""

from __future__ import annotations

from app.services.ast_parser import GraphEdge, GraphNode
from app.services.parsers.base import (
    LanguageParser,
    node_key,
    node_text,
    resolve_module_spec,
    string_value,
    walk,
)


def _parser(grammar: str):
    from tree_sitter_language_pack import get_parser

    return get_parser(grammar)


class JsFamilyParser(LanguageParser):
    """Shared JS/TS walker. Subclasses pick the grammar and whether require() counts."""

    grammar = "typescript"
    allow_require = False

    def parse_file(self, file_path: str, source_code: str):
        grammar = "tsx" if file_path.endswith(".tsx") else self.grammar
        if file_path.endswith(".jsx"):
            grammar = "tsx"
        src = source_code.encode("utf-8")
        tree = _parser(grammar).parse(src)
        if tree.root_node.has_error:
            return [], []

        module = self._module_name(file_path)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        symbols: dict[str, tuple[str, str]] = {}
        fn_ids: dict[tuple[int, int, str], str] = {}

        for node in walk(tree.root_node):
            self._collect_def(node, src, module, file_path, nodes, fn_ids)
            self._collect_import(node, src, module, file_path, edges, symbols)
        for node in walk(tree.root_node):
            if node.type == "call_expression":
                self._collect_call(node, src, module, edges, symbols, fn_ids)
        return nodes, edges

    def _collect_def(self, node, src, module, file_path, nodes, fn_ids):
        if node.type in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            name = node_text(name_node, src)
            qid = _qualified(module, name, node, src)
            nodes.append(GraphNode(id=qid, file=file_path, type="function", line=node.start_point[0] + 1, name=name))
            fn_ids[node_key(node)] = qid
            return
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            name = node_text(name_node, src)
            qid = f"{module}.{name}" if module else name
            nodes.append(GraphNode(id=qid, file=file_path, type="class", line=node.start_point[0] + 1, name=name))
            return
        if node.type != "variable_declarator":
            return
        value = node.child_by_field_name("value")
        name_node = node.child_by_field_name("name")
        if value is None or name_node is None or value.type not in ("arrow_function", "function_expression", "function"):
            return
        if name_node.type != "identifier":
            return
        name = node_text(name_node, src)
        qid = f"{module}.{name}" if module else name
        nodes.append(GraphNode(id=qid, file=file_path, type="function", line=node.start_point[0] + 1, name=name))
        fn_ids[node_key(value)] = qid

    def _collect_import(self, node, src, module, file_path, edges, symbols):
        if node.type == "import_statement":
            clause = next((child for child in node.children if child.type == "import_clause"), None)
            source = next((child for child in node.children if child.type == "string"), None)
            if clause is None or source is None:
                return
            resolved = resolve_module_spec(string_value(source, src), file_path)
            if not resolved:
                return
            for child in walk(clause):
                if child.type == "import_specifier":
                    imported_node = child.child_by_field_name("name")
                    alias = child.child_by_field_name("alias")
                    if imported_node is None:
                        continue
                    imported = node_text(imported_node, src)
                    local = node_text(alias, src) if alias is not None else imported
                    target = f"{resolved}.{imported}"
                    symbols[local] = ("sym", target)
                    edges.append(GraphEdge(source=module, target=target, type="imports"))
                elif child.type == "namespace_import":
                    ident = next((c for c in child.children if c.type == "identifier"), None)
                    if ident is None:
                        continue
                    symbols[node_text(ident, src)] = ("mod", resolved)
                    edges.append(GraphEdge(source=module, target=resolved, type="imports"))
                elif child.type == "identifier" and child.parent is not None and child.parent.type == "import_clause":
                    symbols[node_text(child, src)] = ("mod", resolved)
                    edges.append(GraphEdge(source=module, target=resolved, type="imports"))
            return
        if not self.allow_require or node.type != "variable_declarator":
            return
        value = node.child_by_field_name("value")
        name_node = node.child_by_field_name("name")
        if value is None or value.type != "call_expression" or name_node is None:
            return
        spec = _require_spec(value, src)
        if spec is None:
            return
        resolved = resolve_module_spec(spec, file_path)
        if not resolved:
            return
        if name_node.type == "object_pattern":
            for child in name_node.children:
                if child.type != "shorthand_property_identifier_pattern":
                    continue
                local = node_text(child, src)
                target = f"{resolved}.{local}"
                symbols[local] = ("sym", target)
                edges.append(GraphEdge(source=module, target=target, type="imports"))
            return
        if name_node.type == "identifier":
            local = node_text(name_node, src)
            target = f"{resolved}.{local}"
            symbols[local] = ("sym", target)
            edges.append(GraphEdge(source=module, target=target, type="imports"))

    def _collect_call(self, node, src, module, edges, symbols, fn_ids):
        callee = node.child_by_field_name("function")
        if callee is None:
            return
        owner = _owner(node, fn_ids)
        if not owner:
            return
        target = _resolve_callee(callee, src, module, symbols)
        if target and target != owner:
            edges.append(GraphEdge(source=owner, target=target, type="calls"))


class TypeScriptParser(JsFamilyParser):
    grammar = "typescript"
    allow_require = False


def _qualified(module: str, name: str, node, src: bytes) -> str:
    current = node.parent
    class_name = None
    while current is not None:
        if current.type == "class_declaration":
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                class_name = node_text(name_node, src)
            break
        current = current.parent
    if class_name:
        return f"{module}.{class_name}.{name}" if module else f"{class_name}.{name}"
    return f"{module}.{name}" if module else name


def _owner(node, fn_ids: dict[tuple[int, int, str], str]) -> str | None:
    current = node.parent
    while current is not None:
        found = fn_ids.get(node_key(current))
        if found:
            return found
        current = current.parent
    return None


def _resolve_callee(callee, src: bytes, module: str, symbols: dict[str, tuple[str, str]]) -> str | None:
    if callee.type == "identifier":
        name = node_text(callee, src)
        mapped = symbols.get(name)
        if mapped:
            return mapped[1]
        return f"{module}.{name}" if module else name
    if callee.type != "member_expression":
        return None
    obj = callee.child_by_field_name("object")
    prop = callee.child_by_field_name("property")
    if obj is None or prop is None or obj.type != "identifier":
        return None
    obj_name = node_text(obj, src)
    prop_name = node_text(prop, src)
    mapped = symbols.get(obj_name)
    if mapped:
        return f"{mapped[1]}.{prop_name}"
    return f"{obj_name}.{prop_name}"


def _require_spec(call, src: bytes) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "identifier" or node_text(fn, src) != "require":
        return None
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    literal = next((child for child in args.children if child.type == "string"), None)
    if literal is None:
        return None
    return string_value(literal, src)
