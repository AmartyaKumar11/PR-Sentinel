"""Per-language parsers. No network. Tree-sitter comes from the installed wheel."""

from app.services.graph_builder import build_graph
from app.services.parsers.go_parser import GoParser
from app.services.parsers.java_parser import JavaParser
from app.services.parsers.javascript_parser import JavaScriptParser
from app.services.parsers.python_parser import PythonParser
from app.services.parsers.typescript_parser import TypeScriptParser

PY = """
from src.auth import validate_token

def get_user(user_id, token):
    validate_token(token)
    return {"id": user_id}
"""

TS = """
import { validateToken } from './auth';

export function getUser(userId: string, token: string) {
    validateToken(token);
    return { id: userId };
}
"""

JS = """
const { validateToken } = require('./auth');

function getUser(userId, token) {
    validateToken(token);
    return { id: userId };
}
"""

GO = """
package users

import "myapp/auth"

func GetUser(userID string, token string) map[string]string {
    auth.ValidateToken(token)
    return map[string]string{"id": userID}
}
"""

JAVA = """
package com.example.users;

import com.example.auth.AuthService;

public class UserService {
    public User getUser(String userId, String token) {
        AuthService.validateToken(token);
        return new User(userId);
    }
}
"""


def test_python_parser_keeps_existing_behavior():
    nodes, edges = PythonParser().parse_file("src/users.py", PY)
    assert any(node.name == "get_user" for node in nodes)
    assert edges


def test_typescript_parser_links_named_import():
    nodes, edges = TypeScriptParser().parse_file("src/users.ts", TS)
    assert any(node.name == "getUser" for node in nodes)
    assert any(edge.target == "auth.validateToken" for edge in edges)


def test_javascript_parser_links_require():
    nodes, edges = JavaScriptParser().parse_file("src/users.js", JS)
    assert nodes
    assert edges


def test_go_parser_links_selector_call():
    nodes, edges = GoParser().parse_file("pkg/users/user.go", GO)
    assert any(node.name == "GetUser" for node in nodes)
    assert any(edge.target == "auth.ValidateToken" for edge in edges)


def test_java_parser_finds_class_and_import():
    nodes, edges = JavaParser().parse_file("com/example/users/UserService.java", JAVA)
    assert nodes
    assert edges


def test_mixed_language_graph_keeps_both_files():
    graph = build_graph({"src/users.py": PY, "src/users.ts": TS})
    files = {node["file"] for node in graph["nodes"]}
    assert "src/users.py" in files
    assert "src/users.ts" in files


def test_unknown_extension_returns_empty_graph():
    graph = build_graph({"README.md": "# hi", "config.yaml": "a: 1"})
    assert graph == {"nodes": [], "edges": []}


def test_broken_typescript_does_not_drop_python():
    graph = build_graph(
        {
            "src/broken.ts": "function { broken ( syntax",
            "src/users.py": PY,
        }
    )
    assert any(node["name"] == "get_user" for node in graph["nodes"])
    assert all(not node["file"].endswith(".ts") for node in graph["nodes"])
