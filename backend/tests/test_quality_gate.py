from app.services.quality_gate import classify_ci, diff_sanity, judge_alignment, validate

_DIFF = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -1 +1,2 @@
+def validate_email(email):
     return email
"""

_DIAG = {
    "changed_files": ["src/auth.py"],
    "intent_alignment": {"missing": ["Validate email format"]},
    "blast_radius": {"depth_1_impacted": []},
}


def test_classify_ci():
    assert classify_ci({"state": "pending", "statuses": []}, []) == "no_ci"
    assert classify_ci(
        {"state": "success", "statuses": [{"state": "success"}]},
        [{"status": "completed", "conclusion": "success"}],
    ) == "passed"
    assert classify_ci({"state": "success", "statuses": []}, [{"status": "completed", "conclusion": "failure"}]) == "failed"
    assert classify_ci({"state": "pending", "statuses": []}, [{"status": "in_progress", "conclusion": None}]) == "pending"


def test_alignment_bands():
    assert judge_alignment({"Validate email format": 0.92}) == "pass"
    assert judge_alignment({"Validate email format": 0.61}) == "pass"
    assert judge_alignment({"Validate email format": 0.6}) == "fail"
    assert judge_alignment({"Validate email format": 0.2}) == "fail"
    assert judge_alignment({}) == "pass"


def test_diff_sanity_flags():
    ok = diff_sanity(_DIFF, _DIAG)
    assert ok["ok"]
    assert ok["lines_changed"] == 1
    deleted = """diff --git a/tests/test_auth.py b/tests/test_auth.py
deleted file mode 100644
--- a/tests/test_auth.py
+++ /dev/null
@@ -1 +0,0 @@
-def test_ok():
"""
    bad = diff_sanity(deleted, _DIAG)
    assert bad["test_files_deleted"]
    assert "files_outside_blast_radius" not in bad
    assert not bad["ok"]


def test_diff_sanity_ignores_extra_files():
    diff = """diff --git a/tests/test_auth.py b/tests/test_auth.py
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -1 +1,2 @@
+def test_reset():
     pass
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+note
diff --git a/.cursor/rules.md b/.cursor/rules.md
--- a/.cursor/rules.md
+++ b/.cursor/rules.md
@@ -1 +1 @@
-old
+rule
diff --git a/.gitignore b/.gitignore
--- a/.gitignore
+++ b/.gitignore
@@ -1 +1,2 @@
+*.pyc
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -1 +1,2 @@
+def bill():
     pass
"""
    result = diff_sanity(diff, _DIAG)
    assert result["ok"]
    assert not result["test_files_deleted"]
    assert not result["ci_config_modified"]
    ci = """diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1 @@
-old
+new
"""
    assert diff_sanity(ci, {"changed_files": [".github/workflows/ci.yml"]})["ci_config_modified"]


class _Ans:
    def __init__(self, noul):
        self.noul = noul


class _Jev:
    def __init__(self, noul):
        self.noul = noul
        self.called = False

    async def evaluate(self, state, questions):
        self.called = True
        return {key: _Ans(self.noul) for key in questions}

    async def aclose(self):
        return None


class _GitHub:
    def __init__(self, ci):
        self.ci = ci

    async def get_commit_status(self, owner, repo, sha):
        return self.ci[0]

    async def get_check_runs(self, owner, repo, sha):
        return self.ci[1]

    async def close(self):
        return None


async def test_validate_skips_after_ci_failure():
    jev = _Jev(0.9)
    result = await validate(
        {"owner": "o", "repo": "r", "pr_number": 1, "head_sha": "abc", "diff": _DIFF},
        _DIAG,
        github=_GitHub(({"state": "failure", "statuses": [{}]}, [])),
        jev=jev,
        max_wait=0,
        interval=0,
    )
    assert result["passed"] is False
    assert result["ci_status"] == "failed"
    assert jev.called is False


async def test_validate_passes():
    result = await validate(
        {"owner": "o", "repo": "r", "pr_number": 1, "head_sha": "abc", "diff": _DIFF},
        _DIAG,
        github=_GitHub(
            (
                {"state": "success", "statuses": [{"state": "success"}]},
                [{"status": "completed", "conclusion": "success"}],
            )
        ),
        jev=_Jev(0.92),
        max_wait=0,
        interval=0,
    )
    assert result["verdict"] == "passed"
    assert result["requirement_alignment"]["Validate email format"] == 0.92
    assert "regression_risk" not in result
    assert result["diff_sanity"]["lines_changed"] == 1
    assert "files_outside_blast_radius" not in result["diff_sanity"]
