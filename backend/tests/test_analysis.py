"""Self-check: dep graph + blast radius against demo-repo (M-04/M-06)."""

from pathlib import Path

from app.services.blast_radius import trace_blast_radius
from app.services.ast_parser import build_dependency_graph_from_dir
from app.services.diff_parser import extract_changed_identifiers, is_trivial_diff
from app.utils.hmac_verify import verify_hmac
from app.agent.parser import parse_agent_output


def test_hmac():
    body = b'{"action":"opened"}'
    secret = "dev_secret"
    import hashlib, hmac
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hmac(body, sig, secret)
    assert not verify_hmac(body, "sha256=deadbeef", secret)
    assert not verify_hmac(body, "nope", secret)


def test_parser_raw_json():
    out = parse_agent_output('{"severity": "CRITICAL", "action": "dispatch_urgent"}')
    assert out.type == "answer"
    out2 = parse_agent_output('Answer: {"severity": "MEDIUM"}')
    assert out2.type == "answer"
    out3 = parse_agent_output("Thought: looking at the diff")
    assert out3.type == "thought"


def test_dep_graph_and_blast():
    root = Path(__file__).resolve().parents[2] / "demo-repo"
    graph = build_dependency_graph_from_dir(root, "src/")
    ids = {n["id"] for n in graph["nodes"]}
    assert "src.auth.validate_token" in ids
    assert "src.users.get_user" in ids
    assert "src.orders.create_order" in ids

    # users.get_user calls auth.validate_token
    edge_pairs = {(e["from"], e["to"]) for e in graph["edges"]}
    assert ("src.users.get_user", "src.auth.validate_token") in edge_pairs

    br = trace_blast_radius(["src.auth.validate_token"], graph)
    assert br["risk_score"] >= 0.5
    assert len(br["depth_1_impacted"]) + len(br["depth_2_impacted"]) >= 1


def test_diff_parser():
    diff = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -40,0 +41,8 @@
+def reset_password(email: str) -> dict:
+    token = generate_reset_token(email)
+    return {"email": email, "token": token}
"""
    ids = extract_changed_identifiers(diff)
    assert "src.auth.reset_password" in ids

    readme = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# old
+# new
"""
    assert is_trivial_diff(readme)


if __name__ == "__main__":
    test_hmac()
    test_parser_raw_json()
    test_dep_graph_and_blast()
    test_diff_parser()
    print("ok")
