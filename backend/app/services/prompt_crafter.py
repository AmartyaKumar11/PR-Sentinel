"""DeepSeek-crafted Cursor prompts. Trivial and low severity stay on a template."""

from __future__ import annotations

import json

from app.services.llm_client import LLMClient

PROMPT_CRAFT_SYSTEM = """You are an expert at writing prompts for AI coding agents.
Given a PR diagnosis, write a prompt that a Cursor Cloud Agent will execute to fix the code.

RULES FOR THE PROMPT:
1. Start with a one-sentence summary of what needs to be fixed.
2. List each requirement that is missing, with the EXACT file and function that needs to change.
3. For each fix, describe the approach in 2-3 sentences. Do NOT write the code — describe what the code should do.
4. List constraints: don't break existing tests, don't modify unrelated files, match the repo's code style.
5. End with verification steps: what tests to run, what to check.
6. Keep the total prompt under 800 words — Cursor agents work better with focused instructions.
7. Use markdown formatting with clear headers.

DO NOT include generic boilerplate. Every sentence must be actionable."""


async def craft_prompt(
    diagnosis: dict,
    triage: dict,
    jev_client=None,
    deepseek: LLMClient | None = None,
) -> str:
    severity = triage.get("severity", "MEDIUM")
    if severity in ("TRIVIAL", "LOW"):
        return _simple_template(diagnosis, triage)

    deepseek = deepseek or LLMClient()
    context = {
        "severity": severity,
        "missing_requirements": (diagnosis.get("intent_alignment") or {}).get("missing", []),
        "scope_creep": (diagnosis.get("intent_alignment") or {}).get("scope_creep", []),
        "addressed": (diagnosis.get("intent_alignment") or {}).get("addressed", []),
        "changed_files": diagnosis.get("changed_files", []),
        "blast_radius": {
            "risk_score": (diagnosis.get("blast_radius") or {}).get("risk_score", 0),
            "highest_risk_path": (diagnosis.get("blast_radius") or {}).get("highest_risk_path", ""),
            "untested": (diagnosis.get("blast_radius") or {}).get("untested_impacted", []),
        },
        "issue_title": (diagnosis.get("linked_issue") or {}).get("title", ""),
        "issue_requirements": (diagnosis.get("linked_issue") or {}).get("requirements", []),
        "suggested_fix": triage.get("suggested_fix_approach", ""),
        "affected_files": triage.get("affected_files_priority", []),
    }
    user_msg = (
        "Craft a Cursor Cloud Agent prompt for this PR fix:\n\n"
        f"{json.dumps(context, indent=2)}\n\n"
        "The agent will be launched on the repository with full codebase access.\n"
        "It should create a fix branch and open a pull request when done."
    )
    return await deepseek.chat(PROMPT_CRAFT_SYSTEM, [{"role": "user", "content": user_msg}])


def _simple_template(diagnosis: dict, triage: dict) -> str:
    files = ", ".join(f.get("path", "?") for f in triage.get("affected_files_priority", []))
    return (
        f"Fix the following in {files}:\n\n"
        f"{triage.get('suggested_fix_approach', 'Address the flagged issues.')}\n\n"
        "Constraints: don't break existing tests, match code style.\n"
        "Open a PR when done."
    )
