# PR Sentinel — Architecture Update: Jev + DeepSeek Dual-Model Integration

> **Purpose:** Hand this file to Cursor. It updates AGENT-SPEC.md, DATABASE.md, API-SPEC.md, and PRODUCT-MVP.md to integrate Jev (TypeSafe AI's System One model) alongside DeepSeek V4 Flash.  
> **Rule:** Do not delete existing content. Patch the specified sections.

---

## What Changed and Why

PR Sentinel now uses TWO models, each doing what it's best at:

| Model | Role | What it does | What it does NOT do |
|---|---|---|---|
| **DeepSeek V4 Flash** | Reasoning engine | ReAct loop (Thought → Action), understanding diffs and issues in natural language, generating the Composer prompt narrative | Classification, severity routing, yes/no decisions |
| **Jev** (TypeSafe AI) | Decision engine | All classification, severity routing, per-requirement intent checks, trivial PR detection, verification resolution checks — typed outputs with confidence scores | Text generation, reasoning, tool calling |

### Why This Is Better (Not Just Cheaper)

1. **Intent alignment becomes per-requirement.** Instead of DeepSeek bulk-deciding "which requirements are met?", Jev evaluates EACH requirement as a separate `Noul` with its own probability. "Does diff address 'validate email'?" → 0.12. "Does diff address 'send email'?" → 0.94. Granular confidence per requirement.

2. **TRIAGE collapses from 1-3 seconds to 70-500ms.** One Jev call replaces the entire TRIAGE LLM call. All questions answered in parallel.

3. **Confidence scores are calibrated.** Jev uses RLCD (Reinforcement Learning for Calibrated Decisions). A 0.85 confidence really means ~85% accuracy. This unlocks confidence-gated routing: auto-dispatch high-confidence, escalate low-confidence.

4. **Can't hallucinate structure.** Jev returns typed values from your schema. It can be wrong, but it can't return "MEDIUUM" or forget to include the severity field.

5. **Speculative fan-out is free.** Asking 15 questions costs almost nothing more than asking 1 — all parallel, output is free. Ask everything, let code decide what matters.

---

## New Dependencies

### Python

Add to `backend/requirements.txt`:
```
typesafe-sdk>=0.1.0
```

### Environment Variables

Add to `.env.example` and `.env`:
```env
# ─── Jev (TypeSafe AI) ───
TYPESAFE_API_KEY=sk-xxxx
JEV_MODEL=jev-1.13.0    # Pin the version — jev-latest can shift under you
```

---

## File-by-File Patches

### PATCH 1: AGENT-SPEC.md — Add Jev Client + Restructure Phases

**Add after the LLM Client Implementation section:**

```python
# backend/app/services/jev_client.py

from typesafe_sdk import TypeSafeClient, Choice, Score, Noul

class JevClient:
    def __init__(self):
        self.client = TypeSafeClient(model=settings.JEV_MODEL)  # "jev-1.13.0"

    async def evaluate(self, state: dict, questions: dict) -> dict:
        """Single parallel pass — all questions answered at once."""
        response = self.client.system_one(state=state, questions=questions)
        return response.answers
```

**Replace the TRIAGE phase description with:**

```
═══════════════════════════════════════════════════════════════════
PHASE 2: TRIAGE (Jev — 1 call, 0 LLM calls)
═══════════════════════════════════════════════════════════════════

Trigger:     DIAGNOSE phase completes with diagnosis data
Model:       Jev (NOT DeepSeek)
LLM calls:   0
Jev calls:   1 (all questions parallel, 70-500ms)

BEHAVIOR:
  One Jev system_one() call with the diagnosis as state and ALL
  classification questions asked simultaneously.

IMPLEMENTATION:

  from typesafe_sdk import Choice, Score, Noul

  # Build per-requirement Noul questions dynamically
  requirement_questions = {}
  if diagnosis.get("linked_issue") and diagnosis["linked_issue"].get("requirements"):
      for i, req in enumerate(diagnosis["linked_issue"]["requirements"]):
          requirement_questions[f"req_{i}_addressed"] = Noul(
              instructions=f"The PR diff implements this requirement: '{req}'"
          )

  # Build per-scope-creep-candidate questions
  # Changed files that are NOT mentioned in the issue
  scope_questions = {}
  for i, file in enumerate(diagnosis["changed_files"]):
      scope_questions[f"scope_{i}_in_issue"] = Noul(
          instructions=f"The issue mentions or implies changes to '{file}'"
      )

  answers = jev.evaluate(
      state={
          "diff_summary": diagnosis["diff_summary"],  # truncated diff
          "issue_title": diagnosis.get("linked_issue", {}).get("title", ""),
          "issue_body": diagnosis.get("linked_issue", {}).get("body", ""),
          "changed_files": diagnosis["changed_files"],
          "blast_radius": {
              "impacted_count": len(diagnosis["blast_radius"].get("depth_1_impacted", [])) +
                                len(diagnosis["blast_radius"].get("depth_2_impacted", [])),
              "untested_count": len(diagnosis["blast_radius"].get("untested_impacted", [])),
              "highest_risk_path": diagnosis["blast_radius"].get("highest_risk_path", ""),
          }
      },
      questions={
          # ─── Core classification ───
          "severity": Choice(
              instructions="Overall severity of this PR review",
              criteria={
                  "TRIVIAL": "Only docs, config, or cosmetic changes with no functional impact",
                  "LOW": "Small functional change, tests exist, no missing requirements",
                  "MEDIUM": "Has missing requirements or scope creep but moderate blast radius",
                  "HIGH": "Missing requirements with significant blast radius or risk",
                  "CRITICAL": "Missing requirements in auth/security code with high blast radius",
              }
          ),
          "action": Choice(
              instructions="What action PR Sentinel should take",
              criteria={
                  "skip": "Trivial PR, no review needed",
                  "comment_only": "Post a review comment but don't create a task",
                  "dispatch": "Create a task and deliver to the developer's IDE",
                  "dispatch_urgent": "Create an urgent task + GitHub issue for Background Agent",
              }
          ),

          # ─── Structural checks ───
          "is_trivial": Noul(
              instructions="The PR only modifies non-code files like README, docs, config, or images"
          ),
          "is_phantom_pr": Noul(
              instructions="No GitHub issue is linked to this PR"
          ),
          "is_underspecified_issue": Noul(
              instructions="The linked issue is too vague to extract testable requirements from"
          ),
          "touches_auth_security": Noul(
              instructions="The changed files include authentication, authorization, or security-related code"
          ),

          # ─── Risk assessment ───
          "risk_level": Score(
              instructions="How risky is this change based on the blast radius data",
              criteria=[
                  "Minimal risk — few or no downstream dependents, all tested",
                  "Moderate risk — some downstream dependents, mostly tested",
                  "High risk — many downstream dependents or untested impacted code",
                  "Critical risk — deep impact chain through core modules with untested code",
              ]
          ),

          # ─── Per-requirement intent checks (dynamic) ───
          **requirement_questions,

          # ─── Per-file scope checks (dynamic) ───
          **scope_questions,
      }
  )

  # ─── Assemble triage result from Jev answers ───
  severity = answers["severity"].choice
  action = answers["action"].choice

  # Override: if Jev says TRIVIAL but is_trivial confidence is low, bump to LOW
  if severity == "TRIVIAL" and answers["is_trivial"].noul < 0.7:
      severity = "LOW"
      action = "comment_only"

  # Override: auth + missing reqs = always CRITICAL regardless of Jev's severity pick
  missing_reqs = [
      diagnosis["linked_issue"]["requirements"][i]
      for i, key in enumerate(requirement_questions)
      if answers[key].noul < 0.4  # below 0.4 = requirement NOT addressed
  ]
  if answers["touches_auth_security"].noul > 0.7 and len(missing_reqs) > 0:
      severity = "CRITICAL"
      action = "dispatch_urgent"

  # Build scope creep list
  scope_creep = [
      diagnosis["changed_files"][i]
      for i, key in enumerate(scope_questions)
      if answers[key].noul < 0.3  # file NOT mentioned in issue
  ]

  # Build addressed list
  addressed_reqs = [
      diagnosis["linked_issue"]["requirements"][i]
      for i, key in enumerate(requirement_questions)
      if answers[key].noul > 0.6  # above 0.6 = requirement IS addressed
  ]

  triage_result = {
      "severity": severity,
      "action": action,
      "justification": f"Jev confidence: severity={answers['severity'].confidence:.2f}, "
                        f"risk_level={answers['risk_level'].score:.1f}/3, "
                        f"{len(missing_reqs)} missing reqs, {len(scope_creep)} scope creep files.",
      "intent_alignment": {
          "addressed": addressed_reqs,
          "missing": missing_reqs,
          "scope_creep": scope_creep,
          "requirement_confidences": {
              diagnosis["linked_issue"]["requirements"][i]: round(answers[key].noul, 3)
              for i, key in enumerate(requirement_questions)
          }
      },
      "confidence_scores": {
          "severity": round(answers["severity"].confidence, 3),
          "action": round(answers["action"].confidence, 3),
          "is_trivial": round(answers["is_trivial"].noul, 3),
          "touches_auth": round(answers["touches_auth_security"].noul, 3),
          "risk_level": round(answers["risk_level"].score, 3),
      },
      "suggested_fix_approach": "",  # Still generated by DeepSeek (see below)
      "affected_files_priority": [],  # Populated from blast radius data
  }
```

**After the Jev triage, add a SHORT DeepSeek call for the narrative parts Jev can't generate:**

```
  # DeepSeek generates ONLY the narrative parts Jev can't:
  # 1. suggested_fix_approach (natural language description)
  # 2. affected_files_priority ordering (requires reasoning about importance)

  fix_prompt = f"""Given this diagnosis, write TWO things:
1. A 2-3 sentence fix approach for the developer (what to do, not code)
2. A priority-ordered list of affected files (most important first)

Missing requirements: {missing_reqs}
Scope creep: {scope_creep}
Changed files: {diagnosis['changed_files']}
Blast radius: {diagnosis['blast_radius']['highest_risk_path']}

Respond as JSON: {{"suggested_fix_approach": "...", "affected_files_priority": [...]}}"""

  narrative = await deepseek.chat(system="You are a code review assistant.", messages=[{"role": "user", "content": fix_prompt}])
  narrative_json = json.loads(parse_agent_output(narrative).content)
  triage_result["suggested_fix_approach"] = narrative_json["suggested_fix_approach"]
  triage_result["affected_files_priority"] = narrative_json["affected_files_priority"]
```

### PATCH 2: AGENT-SPEC.md — Add Jev to DIAGNOSE Phase (Fast Exit)

**After the first tool call (fetch_pr_diff) in the DIAGNOSE phase, add a Jev fast-exit check:**

```
  # After fetching the diff, BEFORE calling fetch_linked_issue:
  # Quick Jev check — is this trivial? Skip the rest of DIAGNOSE if so.
  
  trivial_check = jev.evaluate(
      state={"diff": diff_content},
      questions={
          "is_trivial": Noul(
              instructions="This diff only modifies non-code files like README, .md, .yml, .json, images, or configuration"
          ),
          "touches_functions": Noul(
              instructions="This diff adds, modifies, or deletes function or class definitions"
          ),
      }
  )

  if trivial_check["is_trivial"].noul > 0.9:
      # Skip the rest of DIAGNOSE — go straight to TRIAGE with is_trivial=true
      return {"is_trivial": True, "changed_files": [...], ...minimal diagnosis...}

  # Also use touches_functions to decide whether to build dep graph later:
  # If touches_functions.noul < 0.2 → skip build_dependency_graph entirely
  skip_dep_graph = trivial_check["touches_functions"].noul < 0.2
```

### PATCH 3: AGENT-SPEC.md — Replace VERIFY Phase with Jev

**Replace the VERIFY phase with:**

```
═══════════════════════════════════════════════════════════════════
PHASE 4: VERIFY (Jev — 1 call, 1 optional DeepSeek call)
═══════════════════════════════════════════════════════════════════

Trigger:     Follow-up push on a PR with an existing task
Model:       Jev (primary), DeepSeek (only if partially resolved)
Jev calls:   1
DeepSeek:    0 (if all resolved) or 1 (if partially resolved, for follow-up comment)

BEHAVIOR:
  1. Fetch the latest full diff via fetch_pr_diff
  2. Load the previous diagnosis from the task record
  3. ONE Jev call checking each previously-missing requirement + each scope creep item

IMPLEMENTATION:

  # Build verification questions from previous diagnosis
  prev_missing = previous_diagnosis["intent_alignment"]["missing"]
  prev_scope = previous_diagnosis["intent_alignment"]["scope_creep"]

  verify_questions = {}

  for i, req in enumerate(prev_missing):
      verify_questions[f"fixed_{i}"] = Noul(
          instructions=f"The new diff now implements this requirement: '{req}'"
      )

  for i, item in enumerate(prev_scope):
      verify_questions[f"scope_resolved_{i}"] = Noul(
          instructions=f"The scope creep in '{item}' has been reverted or is now justified by the issue"
      )

  verify_questions["new_issues"] = Noul(
      instructions="The new changes introduce problems that were not in the original diff"
  )

  answers = jev.evaluate(
      state={
          "new_diff": new_diff_content,
          "original_issue": previous_diagnosis.get("linked_issue", {}),
          "previously_missing": prev_missing,
          "previously_flagged_scope_creep": prev_scope,
      },
      questions=verify_questions
  )

  # Determine resolution
  resolved_items = [req for i, req in enumerate(prev_missing) if answers[f"fixed_{i}"].noul > 0.6]
  remaining_items = [req for i, req in enumerate(prev_missing) if answers[f"fixed_{i}"].noul <= 0.6]
  scope_resolved = [item for i, item in enumerate(prev_scope) if answers[f"scope_resolved_{i}"].noul > 0.6]
  all_resolved = len(remaining_items) == 0 and answers["new_issues"].noul < 0.3

  verification = {
      "all_resolved": all_resolved,
      "resolved_items": resolved_items,
      "remaining_items": remaining_items,
      "scope_resolved": scope_resolved,
      "new_issues_detected": answers["new_issues"].noul > 0.5,
      "confidence_per_requirement": {
          prev_missing[i]: round(answers[f"fixed_{i}"].noul, 3)
          for i in range(len(prev_missing))
      }
  }

  if all_resolved:
      await update_task_status(db, task_id, "resolved", ...)
      await post_pr_review(pr_number, "✅ PR Sentinel: All issues addressed.")
  else:
      # DeepSeek generates the follow-up comment (needs natural language)
      follow_up = await deepseek.chat(...)
      await update_task_status(db, task_id, "dispatched", ...)
      await post_pr_review(pr_number, follow_up)
```

### PATCH 4: DATABASE.md — Add Jev Confidence Data

**Add these columns to the `tasks` table (ALTER TABLE or add to CREATE TABLE):**

```sql
-- Jev confidence data (stored for dashboard display and debugging)
jev_confidences TEXT,        -- JSON: { severity: 0.92, action: 0.88, risk_level: 2.1, ... }
requirement_scores TEXT,     -- JSON: { "validate email": 0.12, "send email": 0.94, ... }
```

### PATCH 5: API-SPEC.md — Add Jev Data to Review Detail Response

**In `GET /api/reviews/{task_id}` response, add:**

```json
{
  "...existing fields...",
  "jev_confidences": {
    "severity": 0.92,
    "action": 0.88,
    "is_trivial": 0.03,
    "touches_auth": 0.95,
    "risk_level": 2.1
  },
  "requirement_scores": {
    "Validate email format": 0.12,
    "Send reset email": 0.94,
    "Expire token after 1 hour": 0.08
  }
}
```

### PATCH 6: PRODUCT-MVP.md — Update Expected Outputs with Confidence Scores

**Update Scenario 1 expected output:**

```
- Expected Jev output:
  - severity: CRITICAL (confidence: 0.89)
  - action: dispatch_urgent (confidence: 0.91)
  - touches_auth: 0.95
  - Requirement scores:
    - "Validate email format": ~0.1 (NOT addressed)
    - "Send reset email": ~0.9 (addressed)
    - "Expire token after 1 hour": ~0.1 (NOT addressed)
  - risk_level: 2.5/3 (high)
```

---

## New Token Budget

| Phase | Model | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| DIAGNOSE (fast exit check) | Jev | 1 | ~500 | 0 (free) | ~$0.00002 |
| DIAGNOSE (ReAct loop) | DeepSeek | 3-5 | ~4,000 | ~2,000 | ~$0.001 |
| TRIAGE (all classification) | Jev | 1 | ~2,000 | 0 (free) | ~$0.00008 |
| TRIAGE (narrative gen) | DeepSeek | 1 | ~500 | ~300 | ~$0.0002 |
| DISPATCH | None | 0 | 0 | 0 | $0 |
| VERIFY | Jev | 1 | ~1,500 | 0 (free) | ~$0.00006 |
| **Total per review** | | | | | **~$0.0013** |

**Previous total: ~$0.002. New total: ~$0.0013. 35% cheaper, 3x faster on decision steps, and per-requirement confidence scores are a qualitative upgrade.**

With $20 budget: ~15,000 full review cycles (up from ~10,000).

---

## Dashboard Update (Future — Not in This Patch)

The requirement_scores data enables a new dashboard component: a confidence heatmap showing each requirement with its Jev probability score (green > 0.6, red < 0.4, amber in between). This is more informative than the current binary "addressed / missing" list. Build this when the dashboard is in progress (Week 2).

---

## Config Update

**Add to `backend/app/config.py`:**

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Jev (TypeSafe AI)
    TYPESAFE_API_KEY: str
    JEV_MODEL: str = "jev-1.13.0"  # Pin version — jev-latest can shift

    model_config = SettingsConfigDict(env_file=".env")
```

---

## Summary of Changes for Cursor

| File | Section to Patch | What Changes |
|---|---|---|
| `AGENT-SPEC.md` | After LLM Client | Add `jev_client.py` implementation |
| `AGENT-SPEC.md` | DIAGNOSE phase | Add Jev fast-exit check after fetch_pr_diff |
| `AGENT-SPEC.md` | TRIAGE phase | Replace DeepSeek LLM call with Jev system_one() + short DeepSeek narrative call |
| `AGENT-SPEC.md` | VERIFY phase | Replace DeepSeek calls with Jev system_one() for resolution checks |
| `DATABASE.md` | tasks table | Add `jev_confidences TEXT` and `requirement_scores TEXT` columns |
| `API-SPEC.md` | GET /api/reviews/{task_id} | Add jev_confidences and requirement_scores to response |
| `PRODUCT-MVP.md` | Scenario 1-4 expected outputs | Add Jev confidence scores to expected results |
| `SETUP.md` | Dependencies | Add `typesafe-sdk` to requirements.txt |
| `SETUP.md` | Environment Variables | Add `TYPESAFE_API_KEY` and `JEV_MODEL` |

**Do not delete any existing content.** Patch the specified sections. Where a section says "Replace", remove only that section and insert the replacement. Where it says "Add after", insert without removing anything.
