"""Diagnose a failed fix, then write the next agent prompt."""

from __future__ import annotations

import json
import re

from app.database import get_db
from app.services.github_client import GitHubClient
from app.services.llm_client import LLMClient
from app.services.task_manager import get_task

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")

RETRY_ANALYSIS_PROMPT = """You are debugging a failed automated code fix. A Cursor Cloud Agent attempted to fix a PR and the quality gate rejected the result. You have the full context: the original problem, the fix the agent produced, and exactly why it failed.

Your job: write a NEW prompt for the agent's next attempt. This prompt must be specific enough that the agent does not repeat the same mistake.

RULES:
1. Start with what the previous attempt got RIGHT. Don't throw away working code — tell the agent to keep it.
2. Then explain exactly what went WRONG. Not "tests failed" — which test, which assertion, what the expected vs actual output was.
3. For each failure, explain the ROOT CAUSE if you can see it in the diff. "You called validate_email() but never imported the email module" is actionable. "Fix the tests" is not.
4. If a requirement scored low, explain what the agent did vs what it should have done. "You added an email field to the response but never validated the address format before sending the reset email" is actionable. "Address the missing requirements" is not.
5. If CI failed, quote the failing check, the annotation, and the expected vs actual values from the context. Tell the agent to change the implementation so that assertion passes. Do not delete or weaken the test.
6. If diff sanity failed, name the exact violation: how many lines changed, which test file was deleted, or which workflow file was edited. Tell the agent to undo that and keep the fix smaller.
7. Keep the prompt under 1000 words. Dense and specific beats long and vague.

OUTPUT FORMAT:
# Retry: [one-line summary of what still has to change]

## Keep
[what the previous attempt got right, with file and function names]

## What went wrong
[the failing test, assertion, and expected vs actual output, or the unmet requirement and its score]

## Root cause
[what in the diff caused the failure]

## Do this
[numbered steps: file, function, and the change]

## Tests
[existing tests that must keep passing, and any missing test to add]

## Verification
[run the test suite; do not edit CI config]
"""


def _loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


async def gather_failure_context(
    task_id: str,
    gate_result: dict,
    fix_pr_url: str,
    github: GitHubClient | None = None,
) -> dict:
    """Collect everything needed to understand why the fix failed."""
    match = _PR_URL.search(fix_pr_url or "")
    if not match:
        raise ValueError(f"Not a pull request URL: {fix_pr_url}")
    owner, repo, pr_num = match.group(1), match.group(2), int(match.group(3))

    gh = github or GitHubClient()
    own_client = github is None
    context: dict = {}
    try:
        context["fix_diff"] = await gh.get_pr_diff(owner, repo, pr_num)

        if gate_result.get("ci_status") == "failed":
            pr_info = await gh.get_pr_info(owner, repo, pr_num)
            head_sha = pr_info["head_sha"]
            runs = await gh.get_check_runs(owner, repo, head_sha)
            for run in runs:
                if run.get("conclusion") != "failure":
                    continue
                annotations: list = []
                try:
                    response = await gh._client.get(
                        f"/repos/{owner}/{repo}/check-runs/{run['id']}/annotations"
                    )
                    if response.status_code < 400:
                        body = response.json()
                        annotations = body[:10] if isinstance(body, list) else []
                except Exception:
                    annotations = []
                output = run.get("output") or {}
                context["ci_failure"] = {
                    "check_name": run.get("name"),
                    "output_title": output.get("title", ""),
                    "output_summary": output.get("summary", ""),
                    "annotations": annotations,
                }
                break

        alignment = gate_result.get("requirement_alignment") or {}
        context["unmet_requirements"] = {
            req: score for req, score in alignment.items() if score < 0.6
        }
        context["met_requirements"] = {
            req: score for req, score in alignment.items() if score >= 0.6
        }

        task = await get_task(await get_db(), task_id)
        diagnosis = (task or {}).get("diagnosis_json")
        context["original_diagnosis"] = _loads(diagnosis)
        context["previous_prompt"] = (task or {}).get("composer_prompt") or ""
        context["diff_sanity"] = gate_result.get("diff_sanity") or {}
        return context
    finally:
        if own_client:
            await gh.close()


async def build_retry_prompt(context: dict, deepseek: LLMClient | None = None) -> str:
    """Ask DeepSeek for a new agent prompt that names the actual failure."""
    deepseek = deepseek or LLMClient()
    packed = dict(context)
    diff = packed.get("fix_diff") or ""
    if len(diff) > 8000:
        packed["fix_diff"] = diff[:8000] + "\n...[diff truncated]"
    user = (
        "Write the next Cursor Cloud Agent prompt from this failure context.\n\n"
        + json.dumps(packed, indent=2, default=str)
    )
    text = await deepseek.chat(RETRY_ANALYSIS_PROMPT, [{"role": "user", "content": user}])
    return (text or "").strip()
