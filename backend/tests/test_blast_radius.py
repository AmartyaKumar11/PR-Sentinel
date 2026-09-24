from app.services.blast_radius import trace

GRAPH = {
    "nodes": [
        {"id": "auth.validate_token", "file": "src/auth.py", "type": "function", "line": 10},
        {"id": "users.get_user", "file": "src/users.py", "type": "function", "line": 5},
        {"id": "orders.create_order", "file": "src/orders.py", "type": "function", "line": 8},
    ],
    "edges": [
        {"from": "users.get_user", "to": "auth.validate_token", "type": "calls"},
        {"from": "orders.create_order", "to": "users.get_user", "type": "calls"},
    ],
}


def test_trace_depths():
    result = trace(["auth.validate_token"], GRAPH)
    assert "users.get_user" in result["depth_1_impacted"]
    assert "orders.create_order" in result["depth_2_impacted"]


def test_trace_risk_positive():
    result = trace(["auth.validate_token"], GRAPH)
    assert result["risk_score"] > 0
    assert "auth.validate_token" in result["directly_changed"]
