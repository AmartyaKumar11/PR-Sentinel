"""Pre-merge checks. First failure skips the rest."""

from __future__ import annotations

import asyncio
import logging
import time

from app.services.diff_parser import parse_diff

logger = logging.getLogger(__name__)

_CI_PREFIXES = (
    ".github/",
    "dockerfile",
    "docker-compose",
    ".gitlab-ci",
    "jenkinsfile",
    ".circleci/",
    "azure-pipelines",
)


def classify_ci(status: dict | None, runs: list | None) -> str:
    runs = list(runs or [])
    statuses = (status or {}).get("statuses") or []
    state = (status or {}).get("state")
    if state in ("failure", "error") or any(r.get("conclusion") == "failure" for r in runs):
        return "failed"
    if not runs and not statuses:
        return "no_ci"
    if runs:
        if any(r.get("status") != "completed" for r in runs):
            return "pending"
        if all(r.get("conclusion") == "success" for r in runs):
            return "passed"
        return "pending"
    if state == "success":
        return "passed"
    if state == "pending":
        return "pending"
    return "no_ci"


def judge_alignment(scores: dict[str, float], regression: float, contained: float) -> str:
    if any(v < 0.4 for v in scores.values()):
        return "fail"
    if any(v <= 0.6 for v in scores.values()):
        return "partial"
    if regression < 0.3 and contained > 0.5:
        return "pass"
    return "fail"


def _is_test(path: str) -> bool:
    name = path.replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    return "/tests/" in f"/{name}" or base.startswith("test_") or base.endswith("_test.py")


def _is_ci(path: str) -> bool:
    name = path.replace("\\", "/").lower()
    return any(name.startswith(p) or f"/{p}" in f"/{name}" for p in _CI_PREFIXES)


def _allowed_files(diagnosis: dict) -> set[str]:
    allowed = {p.replace("\\", "/") for p in (diagnosis.get("changed_files") or [])}
    blast = diagnosis.get("blast_radius") or {}
    for key in (
        "directly_changed",
        "depth_1_impacted",
        "depth_2_impacted",
        "depth_3_impacted",
        "untested_impacted",
    ):
        for item in blast.get(key) or []:
            if isinstance(item, dict) and item.get("file"):
                allowed.add(str(item["file"]).replace("\\", "/"))
            elif isinstance(item, str) and ("/" in item or item.endswith(".py")):
                allowed.add(item.split(":")[0].replace("\\", "/"))
    return allowed


def diff_sanity(diff: str, diagnosis: dict) -> dict:
    parsed = parse_diff(diff or "")
    lines = 0
    for line in (diff or "").splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            lines += 1
    allowed = _allowed_files(diagnosis)
    outside = []
    deleted_test = False
    ci_touched = False
    for file in parsed["files"]:
        path = file["path"].replace("\\", "/")
        if file["status"] == "deleted" and _is_test(path):
            deleted_test = True
        if _is_ci(path):
            ci_touched = True
        # ponytail: no file list in the diagnosis means we cannot tell what is outside it
        if allowed and path not in allowed:
            outside.append(path)
    ok = lines < 500 and not outside and not deleted_test and not ci_touched
    return {
        "lines_changed": lines,
        "files_outside_blast_radius": outside,
        "test_files_deleted": deleted_test,
        "ci_config_modified": ci_touched,
        "ok": ok,
    }


def _noul(answer) -> float:
    if isinstance(answer, dict):
        return float(answer.get("noul") or 0)
    return float(getattr(answer, "noul", 0) or 0)


def _summary(ci: str, alignment: str | None, scores: dict, sanity: dict | None, verdict: str) -> str:
    if verdict == "passed":
        return (
            "All checks passed. Fix addresses the missing requirements, "
            "no regressions detected, blast radius contained."
        )
    if ci in ("failed", "pending"):
        return f"CI {ci}. Later checks were skipped."
    if alignment == "fail":
        weak = [name for name, score in scores.items() if score < 0.4]
        return "Fix does not address: " + (", ".join(weak) or "the missing requirements") + "."
    if verdict == "partial":
        mid = [name for name, score in scores.items() if 0.4 <= score <= 0.6]
        return "Uncertain whether the fix covers: " + ", ".join(mid) + "."
    reasons = []
    if sanity:
        if sanity["lines_changed"] >= 500:
            reasons.append(f"{sanity['lines_changed']} lines changed")
        if sanity["files_outside_blast_radius"]:
            reasons.append("files outside the blast radius")
        if sanity["test_files_deleted"]:
            reasons.append("a test file was deleted")
        if sanity["ci_config_modified"]:
            reasons.append("CI config was modified")
    return "Diff sanity failed: " + ", ".join(reasons or ["check failed"]) + "."


def _result(ci, scores, regression, contained, sanity, alignment, verdict) -> dict:
    return {
        "passed": verdict == "passed",
        "verdict": verdict,
        "ci_status": ci,
        "requirement_alignment": scores,
        "regression_risk": regression,
        "blast_radius_contained": bool(contained is not None and contained > 0.5),
        "diff_sanity": sanity
        or {
            "lines_changed": 0,
            "files_outside_blast_radius": [],
            "test_files_deleted": False,
            "ci_config_modified": False,
        },
        "summary": _summary(ci, alignment, scores, sanity, verdict),
    }


async def _poll_ci(github, owner, repo, sha, max_wait: float, interval: float) -> str:
    deadline = time.monotonic() + max_wait
    while True:
        status = await github.get_commit_status(owner, repo, sha)
        runs = await github.get_check_runs(owner, repo, sha)
        ci = classify_ci(status, runs)
        if ci != "pending" or time.monotonic() >= deadline:
            return ci
        await asyncio.sleep(interval)


async def validate(
    fix_pr: dict,
    diagnosis: dict,
    *,
    github=None,
    jev=None,
    max_wait: float = 300,
    interval: float = 15,
) -> dict:
    from app.services.github_client import GitHubClient

    own_github = github is None
    github = github or GitHubClient()
    own_jev = False
    try:
        owner, repo = fix_pr["owner"], fix_pr["repo"]
        sha = fix_pr["head_sha"]
        ci = await _poll_ci(github, owner, repo, sha, max_wait, interval)
        if ci in ("failed", "pending"):
            return _result(ci, {}, None, None, None, None, "failed")

        diff = fix_pr.get("diff")
        if not diff:
            diff = await github.get_pr_diff(owner, repo, int(fix_pr["pr_number"]))
        missing = (diagnosis.get("intent_alignment") or {}).get("missing") or []
        if jev is None:
            from app.services.jev_client import JevClient

            jev = JevClient()
            own_jev = True
        from typesafe_sdk import Noul

        questions = {
            f"req_{i}": Noul(instructions=f"Does the fix diff implement: '{req}'")
            for i, req in enumerate(missing)
        }
        questions["introduces_regression"] = Noul(
            instructions="The fix introduces obvious breaking changes or removes existing functionality"
        )
        questions["blast_radius_reduced"] = Noul(
            instructions="The fix does not increase the blast radius by touching additional unrelated modules"
        )
        answers = await jev.evaluate(
            state={"fix_diff": (diff or "")[:4000], "original_diagnosis": diagnosis},
            questions=questions,
        )
        scores = {req: _noul(answers[f"req_{i}"]) for i, req in enumerate(missing)}
        regression = _noul(answers["introduces_regression"])
        contained = _noul(answers["blast_radius_reduced"])
        alignment = judge_alignment(scores, regression, contained)
        if alignment == "fail":
            return _result(ci, scores, regression, contained, None, alignment, "failed")

        sanity = diff_sanity(diff or "", diagnosis)
        if not sanity["ok"]:
            return _result(ci, scores, regression, contained, sanity, alignment, "failed")
        verdict = "partial" if alignment == "partial" else "passed"
        return _result(ci, scores, regression, contained, sanity, alignment, verdict)
    finally:
        if own_github:
            await github.close()
        if own_jev:
            await jev.aclose()
