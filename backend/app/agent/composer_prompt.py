"""Template Composer prompt + GitHub review markdown."""

from __future__ import annotations


def generate_composer_prompt(diagnosis: dict, triage: dict) -> str:
    intent = diagnosis.get("intent_alignment") or {}
    issue_ref = ""
    if diagnosis.get("linked_issue"):
        issue_ref = f"## Requirements from issue #{diagnosis['linked_issue']['number']}\n"
        for req in intent.get("missing") or []:
            issue_ref += f"- [ ] {req}\n"

    scope_section = ""
    if intent.get("scope_creep"):
        scope_section = "## Scope creep to address\n"
        for item in intent["scope_creep"]:
            scope_section += f"- {item} — not in the issue, consider reverting\n"

    files_section = "## Affected files (work in this order)\n"
    for f in triage.get("affected_files_priority") or []:
        lines = ", ".join(str(n) for n in f.get("lines", []))
        files_section += f"- `{f['path']}` line {lines}: {f.get('change_type', '')}\n"

    blast_section = ""
    br = diagnosis.get("blast_radius") or {}
    if br.get("risk_score", 0) > 0:
        impacted = len(br.get("depth_1_impacted") or []) + len(br.get("depth_2_impacted") or [])
        blast_section = f"""## Blast radius warning
This change impacts {impacted} downstream functions.
Highest risk path: {br.get('highest_risk_path', '')}
Ensure tests exist for: {', '.join(br.get('untested_impacted') or []) or 'all covered'}
"""

    return f"""# PR Sentinel fix request

## What needs to be fixed
{triage.get('suggested_fix_approach', '')}

{issue_ref}
{scope_section}
{files_section}
{blast_section}
## Instructions
1. Address each missing requirement listed above
2. Review scope creep items — revert unrelated changes if appropriate
3. Verify that existing tests still pass for impacted functions
4. If no tests exist for impacted code, add basic smoke tests
"""


def format_review_markdown(diagnosis: dict, triage: dict) -> str:
    severity_emoji = {
        "TRIVIAL": "⚪",
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🟠",
        "CRITICAL": "🔴",
    }
    e = severity_emoji.get(triage.get("severity", ""), "⚪")
    intent = diagnosis.get("intent_alignment") or {}
    addressed = "\n".join(f"- ✅ {r}" for r in intent.get("addressed") or []) or "- None identified"
    missing = "\n".join(f"- ⚠️ {r}" for r in intent.get("missing") or []) or "- None"
    creep = "\n".join(f"- 🔀 {r}" for r in intent.get("scope_creep") or []) or "- None"
    issue_ref = (
        f"#{diagnosis['linked_issue']['number']}"
        if diagnosis.get("linked_issue")
        else "No issue linked"
    )
    br = diagnosis.get("blast_radius") or {}
    risk = float(br.get("risk_score") or 0)

    return f"""## {e} PR Sentinel Review

### Intent alignment
Linked issue: {issue_ref}

**Addressed:**
{addressed}

**Missing from PR:**
{missing}

**Scope creep:**
{creep}

### Blast radius
- Changed: {', '.join(br.get('directly_changed') or []) or 'none'}
- Impacted: {len(br.get('depth_1_impacted') or []) + len(br.get('depth_2_impacted') or [])} downstream dependents
- Highest-risk path: `{br.get('highest_risk_path') or 'N/A'}`
- Risk score: {risk:.2f}
- Untested impacted: {', '.join(br.get('untested_impacted') or []) or 'all covered'}

### Overall: {triage.get('severity')}
{triage.get('justification', '')}

---
*Reviewed by PR Sentinel*
"""
