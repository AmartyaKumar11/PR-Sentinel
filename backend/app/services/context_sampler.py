"""Convention tallies from the same trees the parsers already build.

Python uses the ast module. Every other language uses the universal
parser's Tree-sitter grammar. A missing grammar skips that file.
"""

from __future__ import annotations

import ast

from app.services.parsers.base import node_text, walk

_IMPORTS = {
    "import_statement",
    "import_from_statement",
    "import_declaration",
    "use_declaration",
    "import_spec",
}
_THROWS = {"raise_statement", "throw_statement"}
_STRINGS = {"string", "string_literal", "interpreted_string_literal", "raw_string_literal"}


def sample_files(files: dict[str, str]) -> dict[str, dict[str, int]]:
    """Count convention signals in changed source. Config files are not sampled."""
    tallies: dict[str, dict[str, int]] = {
        "import_style": {},
        "file_naming": {},
        "error_handling": {},
        "test_style": {},
    }
    for path, source in files.items():
        _bump(tallies["file_naming"], _filename_style(path))
        _bump(tallies["test_style"], _test_path_style(path))
        if path.endswith(".py"):
            _sample_python(source, tallies)
        else:
            _sample_tree(path, source, tallies)
    return {key: counts for key, counts in tallies.items() if counts}


def _sample_python(source: str, tallies: dict) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for _alias in node.names:
                _bump(tallies["import_style"], "absolute")
        elif isinstance(node, ast.ImportFrom):
            _bump(tallies["import_style"], "relative" if node.level else "absolute")
        elif isinstance(node, ast.Raise):
            _bump(tallies["error_handling"], "throw")
        elif isinstance(node, ast.Return) and _py_returns_error(node):
            _bump(tallies["error_handling"], "return_error")


def _py_returns_error(node: ast.Return) -> bool:
    if node.value is None:
        return False
    try:
        text = ast.unparse(node.value)
    except Exception:
        return False
    return "error" in text.lower()


def _sample_tree(path: str, source: str, tallies: dict) -> None:
    opened = _open_tree(path, source)
    if opened is None:
        return
    root, src = opened
    for node in walk(root):
        if node.type in _IMPORTS and _outer_import(node):
            for spec in _string_values(node, src):
                _bump(tallies["import_style"], _import_style(spec))
        elif node.type in _THROWS:
            _bump(tallies["error_handling"], "throw")
        elif node.type == "return_statement" and b"error" in src[node.start_byte : node.end_byte].lower():
            _bump(tallies["error_handling"], "return_error")
        elif node.type in {"call_expression", "call"}:
            name = _call_name(node, src)
            if name in {"describe", "it"}:
                _bump(tallies["test_style"], "describe")
            elif name == "test":
                _bump(tallies["test_style"], "test")


def _open_tree(path: str, source: str):
    """Parse with the universal parser's grammar. None if it cannot."""
    from app.services.parsers.universal_parser import EXTENSION_MAP, _ext, _parser

    grammar = EXTENSION_MAP.get(_ext(path))
    if not grammar or grammar == "python":
        return None
    try:
        parser = _parser(grammar)
    except Exception:
        return None
    src = source.lstrip("\ufeff").encode("utf-8")
    try:
        tree = parser.parse(src)
    except Exception:
        return None
    if tree.root_node.has_error:
        return None
    return tree.root_node, src


def _outer_import(node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in _IMPORTS:
            return False
        parent = parent.parent
    return True


def _string_values(node, src: bytes) -> list[str]:
    found = []
    for child in walk(node):
        if child is node or child.type not in _STRINGS:
            continue
        text = node_text(child, src).strip().strip("'\"`")
        if text:
            found.append(text)
    return found


def _call_name(node, src: bytes) -> str:
    function = node.child_by_field_name("function")
    target = function if function is not None else node
    text = node_text(target, src)
    return text.rsplit(".", 1)[-1].strip()


def _import_style(spec: str) -> str:
    if spec.startswith("."):
        return "relative"
    if spec.startswith("@") or spec.startswith("~/"):
        return "alias"
    return "absolute"


def _filename_style(path: str) -> str | None:
    stem = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.split(".", 1)[0]
    if not stem or stem.lower() in {"index", "mod", "init"}:
        return None
    if "-" in stem:
        return "kebab"
    if "_" in stem:
        return "snake"
    if stem[:1].isupper() and any(char.islower() for char in stem):
        return "pascal"
    if any(char.isupper() for char in stem[1:]):
        return "camel"
    return None


def _test_path_style(path: str) -> str | None:
    normal = path.replace("\\", "/")
    name = normal.rsplit("/", 1)[-1]
    is_test = (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
        or "/__tests__/" in f"/{normal}"
    )
    if not is_test:
        return None
    if normal.startswith("tests/") or "/tests/" in normal:
        return "separate"
    return "colocated"


def _bump(counts: dict[str, int], key: str | None) -> None:
    if key:
        counts[key] = counts.get(key, 0) + 1
