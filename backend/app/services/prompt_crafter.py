"""DeepSeek-crafted Cursor prompts. Trivial and low severity stay on a template."""

from __future__ import annotations

import json

from app.services.llm_client import LLMClient

PROMPT_CRAFT_SYSTEM = """You write fix instructions for a Cursor Cloud Agent working on a real codebase. The agent will read your instructions and generate code autonomously. Your prompt determines the quality of that code.

RULES:

1. WHAT to fix — be explicit about each requirement. Not "add email validation." Instead: "Validate the email parameter in reset_password() before any other logic runs. Use Python's email.utils.parseaddr or a regex that handles standard RFC 5322 addresses. Return a clear error response (not an exception) when the format is invalid. Do not send the reset email if validation fails."

2. WHERE to fix — name the exact file, function, and where in the function the change goes. "In src/auth.py, inside reset_password(), before the call to generate_reset_token()."

3. HOW to fix — describe the approach, not the code. But be specific about the pattern. "Use a constant-time comparison for token validation to avoid timing attacks. Store expires_at as a UTC datetime, not a relative offset."

4. WHAT NOT to do — constraints prevent the agent from going rogue:
   - Do not modify function signatures unless required
   - Do not add new dependencies without justification
   - Do not refactor unrelated code
   - Do not change test infrastructure or CI configuration
   - Match the existing code style (indentation, naming, docstring format)

5. TESTS — always require tests for the new behavior:
   "Add test cases to tests/test_auth.py:
   - test_reset_password_invalid_email: pass a malformed email, verify it returns an error without sending
   - test_reset_password_expired_token: create a token, advance time past 1 hour, verify validation rejects it
   - test_reset_password_valid_flow: verify the happy path still works after the changes"

6. VERIFICATION — tell the agent how to check its own work:
   "After making changes, run: pytest tests/ -v
   All existing tests must pass. The new tests must pass.
   If any test fails, fix the code, not the test."

7. SECURITY — flag security-sensitive changes:
   "This touches authentication code. Do not:
   - Log email addresses or tokens at INFO level
   - Return different error messages for 'email not found' vs 'invalid email' (prevents user enumeration)
   - Store tokens in plaintext"

8. Keep the prompt under 1000 words. Dense and specific beats long and vague.

OUTPUT FORMAT:
# Fix: [one-line summary]

## Requirements
[numbered list, each with file, function, and approach]

## Constraints
[what not to do]

## Tests required
[specific test cases with names and assertions]

## Verification
[how to check the fix works]
"""


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
        "changed_identifiers": diagnosis.get("changed_identifiers", []),
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
    diff = (diagnosis.get("diff_summary") or "")[:2000]
    user_msg = (
        "Craft a Cursor Cloud Agent prompt for this PR fix.\n\n"
        f"{json.dumps(context, indent=2)}\n\n"
        "Current diff, truncated to the changed code. Reference these real functions "
        "and the order they run, not just the file names:\n"
        f"{diff}\n\n"
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
