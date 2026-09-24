from app.services.diff_parser import extract_changed_identifiers, parse_diff

SAMPLE = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -40,6 +40,10 @@ def generate_reset_token(user_id):
+def reset_password(email):
+    token = generate_reset_token(email)
+    return token
"""


def test_parse_diff_extracts_file():
    result = parse_diff(SAMPLE)
    assert len(result["files"]) == 1
    assert result["files"][0]["path"] == "src/auth.py"
    assert result["files"][0]["hunks"]


def test_extract_changed_identifiers():
    ids = extract_changed_identifiers(parse_diff(SAMPLE))
    assert "src.auth.reset_password" in ids
