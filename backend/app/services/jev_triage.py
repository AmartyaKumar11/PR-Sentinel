"""Assemble triage / verify results from Jev answers (pure helpers)."""

from __future__ import annotations

from typesafe_sdk import Choice, Noul, Score


def build_triage_questions(diagnosis: dict) -> tuple[dict, dict, dict]:
    """Returns (questions, requirement_questions, scope_questions)."""
    requirement_questions: dict = {}
    linked = diagnosis.get("linked_issue") or {}
    for i, req in enumerate(linked.get("requirements") or []):
        requirement_questions[f"req_{i}_addressed"] = Noul(
            instructions=f"The PR diff implements this requirement: '{req}'"
        )

    scope_questions: dict = {}
    for i, file in enumerate(diagnosis.get("changed_files") or []):
        scope_questions[f"scope_{i}_in_issue"] = Noul(
            instructions=f"The issue mentions or implies changes to '{file}'"
        )

    questions = {
        "severity": Choice(
            instructions="Overall severity of this PR review",
            criteria={
                "TRIVIAL": "Only docs, config, or cosmetic changes with no functional impact",
                "LOW": "Small functional change, tests exist, no missing requirements",
                "MEDIUM": "Has missing requirements or scope creep but moderate blast radius",
                "HIGH": "Missing requirements with significant blast radius or risk",
                "CRITICAL": "Missing requirements in auth/security code with high blast radius",
            },
        ),
        "action": Choice(
            instructions="What action PR Sentinel should take",
            criteria={
                "skip": "Trivial PR, no review needed",
                "comment_only": "Post a review comment but don't create a task",
                "dispatch": "Create a task and deliver to the developer's IDE",
                "dispatch_urgent": "Create an urgent task + GitHub issue for Background Agent",
            },
        ),
        "is_trivial": Noul(
            instructions="The PR only modifies non-code files like README, docs, config, or images"
        ),
        "is_phantom_pr": Noul(instructions="No GitHub issue is linked to this PR"),
        "is_underspecified_issue": Noul(
            instructions="The linked issue is too vague to extract testable requirements from"
        ),
        "touches_auth_security": Noul(
            instructions="The changed files include authentication, authorization, or security-related code"
        ),
        "risk_level": Score(
            instructions="How risky is this change based on the blast radius data",
            criteria=[
                "Minimal risk — few or no downstream dependents, all tested",
                "Moderate risk — some downstream dependents, mostly tested",
                "High risk — many downstream dependents or untested impacted code",
                "Critical risk — deep impact chain through core modules with untested code",
            ],
        ),
        **requirement_questions,
        **scope_questions,
    }
    return questions, requirement_questions, scope_questions


def assemble_triage(diagnosis: dict, answers: dict, requirement_questions: dict, scope_questions: dict) -> dict:
    severity = answers["severity"].choice
    action = answers["action"].choice

    if severity == "TRIVIAL" and answers["is_trivial"].noul < 0.7:
        severity = "LOW"
        action = "comment_only"

    linked = diagnosis.get("linked_issue") or {}
    reqs = linked.get("requirements") or []
    req_keys = list(requirement_questions.keys())

    missing_reqs = [
        reqs[i] for i, key in enumerate(req_keys) if answers[key].noul < 0.4
    ]
    addressed_reqs = [
        reqs[i] for i, key in enumerate(req_keys) if answers[key].noul > 0.6
    ]

    if answers["touches_auth_security"].noul > 0.7 and missing_reqs:
        severity = "CRITICAL"
        action = "dispatch_urgent"

    # Phantom PR (no linked issue) → at least MEDIUM review
    phantom = diagnosis.get("is_phantom_pr") or answers["is_phantom_pr"].noul > 0.7
    if phantom and severity in ("TRIVIAL", "LOW"):
        severity = "MEDIUM"
        if action in ("skip", "comment_only"):
            action = "dispatch"

    files = diagnosis.get("changed_files") or []
    scope_creep = [
        files[i] for i, key in enumerate(scope_questions) if answers[key].noul < 0.3
    ]
    if phantom and not scope_creep:
        scope_creep = list(files)

    return {
        "severity": severity,
        "action": action,
        "justification": (
            f"Jev confidence: severity={answers['severity'].confidence:.2f}, "
            f"risk_level={answers['risk_level'].score:.1f}/3, "
            f"{len(missing_reqs)} missing reqs, {len(scope_creep)} scope creep files."
        ),
        "intent_alignment": {
            "addressed": addressed_reqs,
            "missing": missing_reqs,
            "scope_creep": scope_creep,
            "requirement_confidences": {
                reqs[i]: round(answers[key].noul, 3) for i, key in enumerate(req_keys)
            },
        },
        "confidence_scores": {
            "severity": round(answers["severity"].confidence, 3),
            "action": round(answers["action"].confidence, 3),
            "is_trivial": round(answers["is_trivial"].noul, 3),
            "touches_auth": round(answers["touches_auth_security"].noul, 3),
            "risk_level": round(answers["risk_level"].score, 3),
        },
        "suggested_fix_approach": "",
        "affected_files_priority": [],
    }


def build_verify_questions(prev_missing: list, prev_scope: list) -> dict:
    q: dict = {}
    for i, req in enumerate(prev_missing):
        q[f"fixed_{i}"] = Noul(
            instructions=f"The new diff now implements this requirement: '{req}'"
        )
    for i, item in enumerate(prev_scope):
        q[f"scope_resolved_{i}"] = Noul(
            instructions=(
                f"The scope creep in '{item}' has been reverted or is now justified by the issue"
            )
        )
    q["new_issues"] = Noul(
        instructions="The new changes introduce problems that were not in the original diff"
    )
    return q


def assemble_verification(prev_missing: list, prev_scope: list, answers: dict) -> dict:
    resolved_items = [
        req for i, req in enumerate(prev_missing) if answers[f"fixed_{i}"].noul > 0.6
    ]
    remaining_items = [
        req for i, req in enumerate(prev_missing) if answers[f"fixed_{i}"].noul <= 0.6
    ]
    scope_resolved = [
        item
        for i, item in enumerate(prev_scope)
        if answers[f"scope_resolved_{i}"].noul > 0.6
    ]
    all_resolved = len(remaining_items) == 0 and answers["new_issues"].noul < 0.5
    return {
        "all_resolved": all_resolved,
        "resolved_items": resolved_items,
        "remaining_items": remaining_items,
        "scope_resolved": scope_resolved,
        "new_issues_detected": answers["new_issues"].noul > 0.5,
        "confidence_per_requirement": {
            prev_missing[i]: round(answers[f"fixed_{i}"].noul, 3)
            for i in range(len(prev_missing))
        },
    }


def trivial_check_questions() -> dict:
    return {
        "is_trivial": Noul(
            instructions=(
                "This diff only modifies non-code files like README, .md, .yml, .json, images, or configuration"
            )
        ),
        "touches_functions": Noul(
            instructions="This diff adds, modifies, or deletes function or class definitions"
        ),
    }
