"""Per-language parsers. No network. Tree-sitter comes from the installed wheel."""

from app.services.graph_builder import build_graph
from app.services.parsers.python_parser import PythonParser

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
    graph = build_graph(
        {
            "src/auth.ts": "export function validateToken(token: string) { return token; }\n",
            "src/users.ts": TS,
        }
    )
    assert any(node["name"] == "getUser" for node in graph["nodes"])
    assert any(edge["to"] == "auth.validateToken" for edge in graph["edges"])


def test_javascript_parser_links_require():
    graph = build_graph(
        {
            "src/auth.js": "function validateToken(token) { return token; }\n",
            "src/users.js": JS,
        }
    )
    assert any(node["name"] == "getUser" for node in graph["nodes"])
    assert graph["edges"]


def test_go_parser_links_selector_call():
    graph = build_graph(
        {
            "pkg/auth.go": "package auth\n\nfunc ValidateToken(token string) {}\n",
            "pkg/users/user.go": GO,
        }
    )
    assert any(node["name"] == "GetUser" for node in graph["nodes"])
    assert any(edge["to"] == "auth.ValidateToken" for edge in graph["edges"])


def test_java_parser_finds_class_and_import():
    graph = build_graph(
        {
            "com/example/auth/AuthService.java": (
                "package com.example.auth;\n"
                "public class AuthService { public static void validateToken(String t) {} }\n"
            ),
            "com/example/users/UserService.java": JAVA,
        }
    )
    assert any(node["name"] == "UserService" for node in graph["nodes"])
    assert graph["edges"]


def test_mixed_language_graph_keeps_both_files():
    graph = build_graph({"src/users.py": PY, "src/users.ts": TS})
    files = {node["file"] for node in graph["nodes"]}
    assert "src/users.py" in files
    assert "src/users.ts" in files


def test_unknown_extension_returns_empty_graph():
    graph = build_graph({"README.md": "# hi", "config.yaml": "a: 1"})
    assert graph == {"nodes": [], "edges": []}


def test_name_match_links_bare_call():
    graph = build_graph(
        {
            "src/auth.ts": "export function validateToken(token: string) { return token; }\n",
            "src/users.ts": "export function getUser(token: string) { validateToken(token); }\n",
        }
    )
    assert any(edge["to"] == "auth.validateToken" and edge["from"] == "users.getUser" for edge in graph["edges"])


def test_ambiguous_name_keeps_every_candidate():
    graph = build_graph(
        {
            "lib/a/check.ts": "export function validate(token: string) { return token; }\n",
            "lib/b/check.ts": "export function validate(token: string) { return token; }\n",
            "lib/c/user.ts": "export function getUser(token: string) { validate(token); }\n",
        }
    )
    targets = {edge["to"] for edge in graph["edges"] if edge["from"] == "c.user.getUser"}
    assert "a.check.validate" in targets
    assert "b.check.validate" in targets


def test_rust_works_without_a_dedicated_parser():
    graph = build_graph(
        {
            "src/auth.rs": "fn validate_token() {}\n",
            "src/user.rs": "fn get_user() { validate_token(); }\n",
        }
    )
    assert any(node["name"] == "get_user" for node in graph["nodes"])
    assert any(edge["to"] == "auth.validate_token" and edge["from"] == "user.get_user" for edge in graph["edges"])


def test_broken_typescript_does_not_drop_python():
    graph = build_graph(
        {
            "src/broken.ts": "function { broken ( syntax",
            "src/users.py": PY,
        }
    )
    assert any(node["name"] == "get_user" for node in graph["nodes"])
    assert all(not node["file"].endswith(".ts") for node in graph["nodes"])
