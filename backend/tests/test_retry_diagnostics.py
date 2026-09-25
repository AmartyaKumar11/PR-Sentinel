import json

import pytest

from app.services.retry_diagnostics import (
    RETRY_ANALYSIS_PROMPT,
    build_fallback_retry_prompt,
    build_retry_prompt,
    gather_failure_context,
)


def test_retry_prompt_is_complete():
    assert "OUTPUT FORMAT" in RETRY_ANALYSIS_PROMPT
    assert "## Verification" in RETRY_ANALYSIS_PROMPT
    assert RETRY_ANALYSIS_PROMPT.rstrip().endswith("[run the test suite; do not edit CI config]")
    assert "You added an email" in RETRY_ANALYSIS_PROMPT


class _FakeLLM:
    def __init__(self):
        self.system = ""
        self.user = ""

    async def chat(self, system, messages, **kwargs):
        self.system = system
        self.user = messages[0]["content"]
        self.kwargs = kwargs
        return "Keep create_order. Raise ValueError when quantity is below 1."


@pytest.mark.asyncio
async def test_build_retry_prompt_returns_the_model_text(monkeypatch):
    async def _gather(*_args, **_kwargs):
        return {"fix_diff": "diff", "unmet_requirements": {"qty": 0.2}}

    monkeypatch.setattr("app.services.retry_diagnostics.gather_failure_context", _gather)
    llm = _FakeLLM()
    text = await build_retry_prompt("task-1", {}, "https://github.com/o/r/pull/1", llm)
    assert text.startswith("Keep create_order")
    assert llm.system is RETRY_ANALYSIS_PROMPT
    assert llm.kwargs.get("thinking") is False
    assert "qty" in llm.user


def test_fallback_names_unmet_requirements():
    text = build_fallback_retry_prompt(
        {
            "ci_status": "passed",
            "requirement_alignment": {
                "Reject a coupon when the code is empty or expired": 0.51,
                "Charge the customer in integer cents": 0.94,
            },
        },
        "Charge only the price difference.",
        {"changed_files": ["src/coupons.py"], "changed_identifiers": ["coupons.apply_coupon"]},
    )
    assert "REQUIREMENT NOT MET: Reject a coupon when the code is empty or expired (scored 0.51 — partially implemented)." in text
    assert "In src/coupons.py, in apply_coupon():" in text
    assert "Test: test_reject_a_coupon_when_the_code must pass." in text
    assert "Charge the customer in integer cents (score: 0.94)" in text
    assert "Charge only the price difference." in text


def test_refining_message_counts_to_three():
    from app.discord.views import refining_message

    assert refining_message(1) == "🔄 Refining fix (attempt 2/3)..."
    assert refining_message(2) == "🔄 Refining fix (attempt 3/3)..."
    assert refining_message(3) is None


class _Resp:
    status_code = 200

    def json(self):
        return [{"message": "AssertionError: expected ValueError", "path": "tests/test_orders.py"}]


class _GitHub:
    def __init__(self):
        self._client = self

    async def get(self, _path):
        return _Resp()

    async def get_pr_diff(self, *_args):
        return "diff --git a/src/orders.py"

    async def get_pr_info(self, *_args):
        return {"head_sha": "abc"}

    async def get_check_runs(self, *_args):
        return [
            {
                "id": 9,
                "name": "pytest",
                "conclusion": "failure",
                "output": {"title": "failed", "summary": "1 failed"},
            }
        ]

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_gather_failure_context(monkeypatch):
    async def _task(_db, _task_id):
        return {
            "diagnosis_json": json.dumps({"intent_alignment": {"missing": ["Reject negative quantities"]}}),
            "composer_prompt": "Add the quantity check.",
        }

    async def _db():
        return None

    monkeypatch.setattr("app.services.retry_diagnostics.get_task", _task)
    monkeypatch.setattr("app.services.retry_diagnostics.get_db", _db)

    gate = {
        "ci_status": "failed",
        "requirement_alignment": {"Reject negative quantities": 0.2, "Clear error": 0.9},
        "diff_sanity": {"lines_changed": 4, "ok": True},
    }
    context = await gather_failure_context(
        "task-1",
        gate,
        "https://github.com/AmartyaKumar11/pr-sentinel-demo/pull/33",
        github=_GitHub(),
    )
    assert context["ci_failure"]["check_name"] == "pytest"
    assert context["ci_failure"]["annotations"][0]["path"] == "tests/test_orders.py"
    assert "Reject negative quantities" in context["unmet_requirements"]
    assert context["met_requirements"]["Clear error"] == 0.9
    assert context["previous_prompt"] == "Add the quantity check."
    assert context["original_diagnosis"]["intent_alignment"]["missing"] == ["Reject negative quantities"]
    assert context["diff_sanity"]["lines_changed"] == 4
