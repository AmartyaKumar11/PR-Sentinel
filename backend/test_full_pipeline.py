"""Full PR Sentinel run from the terminal. Real APIs, no Discord.

    python test_full_pipeline.py <owner> <repo> <pr_number>
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid


def _loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


async def run_trigger(owner: str, repo: str, pr_number: int) -> dict:
    from app.services.github_client import GitHubClient

    github = GitHubClient()
    try:
        info = await github.get_pr_info(owner, repo, pr_number)
    finally:
        await github.close()
    if not info.get("head_sha"):
        raise AssertionError("PR has no head SHA")
    return {
        "task_id": str(uuid.uuid4()),
        "head_sha": info["head_sha"],
        "title": info.get("title") or "",
        "detail": f"task_id pending until dispatch, sha {info['head_sha'][:12]}",
    }


async def run_diagnose(agent, task_id, owner, repo, pr_number, head_sha) -> dict:
    diagnosis = await agent._run_diagnose(task_id, owner, repo, pr_number, head_sha)
    for key in ("changed_files", "intent_alignment", "blast_radius", "is_phantom_pr"):
        if key not in diagnosis:
            raise AssertionError(f"diagnosis missing {key}")
    if not diagnosis.get("is_trivial") and not diagnosis.get("changed_files"):
        raise AssertionError("changed_files is empty")
    issue = diagnosis.get("linked_issue")
    intent = diagnosis.get("intent_alignment") or {}
    if issue:
        if not isinstance(intent.get("addressed"), list) or not isinstance(intent.get("missing"), list):
            raise AssertionError("intent_alignment lists missing")
    elif not diagnosis.get("is_phantom_pr"):
        raise AssertionError("PR with no issue was not marked phantom")
    risk = (diagnosis.get("blast_radius") or {}).get("risk_score")
    if not isinstance(risk, (int, float)) or not 0 <= risk <= 1:
        raise AssertionError(f"risk_score {risk}")
    buffered = len(agent._pending_traces.get(task_id) or [])
    if buffered <= 0:
        from app.database import get_db

        db = await get_db()
        cur = await db.execute("SELECT COUNT(*) FROM trace_steps WHERE task_id = ?", (task_id,))
        buffered = (await cur.fetchone())[0]
    if buffered <= 0:
        raise AssertionError("no trace steps")
    missing = intent.get("missing") or []
    br = diagnosis.get("blast_radius") or {}
    path = br.get("highest_risk_path") or "N/A"
    depth_1 = br.get("depth_1_impacted") or []
    return {
        "diagnosis": diagnosis,
        "detail": f"{len(missing)} missing, risk {risk}, path {path}, depth_1 {depth_1}",
    }


async def run_triage(agent, task_id, diagnosis) -> dict:
    triage = await agent._run_jev_triage(task_id, diagnosis)
    if triage.get("severity") not in {"TRIVIAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        raise AssertionError(f"severity {triage.get('severity')}")
    if triage.get("action") not in {"skip", "comment_only", "dispatch", "dispatch_urgent"}:
        raise AssertionError(f"action {triage.get('action')}")
    if not triage.get("confidence_scores"):
        raise AssertionError("confidence scores empty")
    missing = " ".join((diagnosis.get("intent_alignment") or {}).get("missing") or []).lower()
    if any(word in missing for word in ("email", "password", "token")):
        if triage["severity"] not in {"CRITICAL", "HIGH"}:
            raise AssertionError(f"scenario 1 severity {triage['severity']}")
    return {
        "triage": triage,
        "detail": f"{triage['severity']}, {triage['action']}",
    }


async def run_dispatch(agent, task_id, owner, repo, pr_number, head_sha, diagnosis, triage) -> dict:
    await agent._run_dispatch(task_id, owner, repo, pr_number, head_sha, diagnosis, triage)
    from app.database import get_db

    db = await get_db()
    cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cur.fetchone()
    if not row:
        raise AssertionError("task row missing")
    task = dict(row)
    if triage.get("action") != "skip" and task["status"] != "dispatched":
        raise AssertionError(f"status {task['status']}")
    if triage.get("action") != "skip" and not task.get("github_comment_url"):
        raise AssertionError("comment_url is null")
    prompt = task.get("composer_prompt") or ""
    if len(prompt) <= 100:
        raise AssertionError(f"prompt length {len(prompt)}")
    issue = ""
    if triage.get("action") == "dispatch_urgent" and not task.get("github_issue_id"):
        issue = ", no GitHub issue opened"
    return {"task": task, "detail": f"comment posted, prompt {len(prompt)}ch{issue}"}


async def run_prompt_quality(prompt: str) -> dict:
    from typesafe_sdk import Noul

    from app.services.jev_client import JevClient

    answers = await JevClient().evaluate(
        state={"prompt": prompt[:4000]},
        questions={
            "specific": Noul(
                instructions=(
                    "Is this prompt specific enough for a coding agent to act on "
                    "without asking clarifying questions?"
                )
            )
        },
    )
    score = float(getattr(answers["specific"], "noul", 0) or 0)
    if score <= 0.6:
        raise AssertionError(f"prompt quality {score:.2f}")
    return {"score": score, "detail": f"score {score:.2f}"}


async def run_agent(task: dict) -> dict:
    from app.config import settings
    from app.services.cursor_client import cursor

    if not settings.CURSOR_API_KEY or "your_" in settings.CURSOR_API_KEY:
        return {"skipped": True, "detail": "SKIPPED — no Cursor API key"}
    started = time.monotonic()
    result = await cursor.launch_agent(
        repo_full_name=task["repo"],
        prompt=task.get("composer_prompt") or "",
        branch=f"pr-sentinel/fix-{task['pr_number']}",
    )
    from app.database import get_db

    db = await get_db()
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
        (result["agent_id"], task["id"]),
    )
    await db.commit()
    status = {"status": "running"}
    while time.monotonic() - started < 300:
        status = await cursor.get_run_status(result["agent_id"])
        if status["status"] in {"completed", "failed", "cancelled", "expired"}:
            break
        await asyncio.sleep(10)
    else:
        raise AssertionError("agent still running after 5 minutes")
    if status["status"] != "completed":
        raise AssertionError(f"agent {status['status']}")
    elapsed = int(time.monotonic() - started)
    return {
        "status": status,
        "agent_id": result["agent_id"],
        "detail": f"{result['agent_id']}, completed {elapsed}s",
    }


async def run_quality_gate(task_id: str, status: dict, diagnosis: dict) -> dict:
    from app.database import get_db
    from app.discord.views import parse_pr_url
    from app.services.github_client import GitHubClient
    from app.services.quality_gate import validate

    parsed = parse_pr_url(status.get("pr_url") or "")
    if not parsed:
        raise AssertionError("agent completed without a PR URL")
    github = GitHubClient()
    try:
        owner, repo, number = parsed
        info = await github.get_pr_info(owner, repo, number)
        diff = await github.get_pr_diff(owner, repo, number)
        gate = await validate(
            {
                "owner": owner,
                "repo": repo,
                "pr_number": number,
                "head_sha": info["head_sha"],
                "diff": diff,
            },
            diagnosis,
            github=github,
        )
    finally:
        await github.close()
    db = await get_db()
    await db.execute(
        "UPDATE tasks SET quality_gate_json = ? WHERE id = ?",
        (json.dumps(gate), task_id),
    )
    await db.commit()
    if not gate.get("ci_status"):
        raise AssertionError("CI status check did not run")
    if gate["ci_status"] in {"passed", "no_ci"} and not gate.get("requirement_alignment") and not gate.get("diff_sanity"):
        raise AssertionError("alignment and diff checks did not run")
    return {
        "gate": gate,
        "fix": parsed,
        "head_sha": info["head_sha"],
        "detail": f"CI {gate['ci_status']}, {gate['verdict']}",
    }


async def run_verify(agent, task_id, fix, head_sha) -> dict:
    owner, repo, number = fix
    await agent._run_jev_verify(task_id, owner, repo, number, head_sha)
    from app.database import get_db

    db = await get_db()
    cur = await db.execute("SELECT status, verification_json FROM tasks WHERE id = ?", (task_id,))
    row = dict(await cur.fetchone())
    verification = _loads(row.get("verification_json"))
    if row["status"] not in {"resolved", "dispatched"}:
        raise AssertionError(f"status {row['status']}")
    return {
        "verification": verification,
        "detail": "all resolved" if verification.get("all_resolved") else "issues remain",
    }


def _cost(tokens: int, jev_calls: int) -> str:
    # ponytail: flash list price drifts. Upgrade when billing is wired.
    return f"${tokens / 1_000_000 * 0.14 + jev_calls * 0.001:.4f}"


async def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 3:
        print("Usage: python test_full_pipeline.py <owner> <repo> <pr_number>")
        return 1
    owner, repo, pr_number = argv[0], argv[1], int(argv[2])
    from app.agent.orchestrator import AgentOrchestrator
    from app.services.jev_client import JevClient
    from app.services.llm_client import LLMClient

    LLMClient.total_tokens = 0
    JevClient.calls = 0
    agent = AgentOrchestrator()
    started = time.perf_counter()
    rows: list[tuple[str, str, str]] = []
    stopped = None

    async def step(name, coro):
        nonlocal stopped
        if stopped:
            return None
        try:
            result = await coro
            detail = result.get("detail") if isinstance(result, dict) else str(result)
            rows.append((name, "OK", detail))
            print(f"\n== {name} ==\n{detail}")
            return result
        except Exception as exc:
            rows.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
            stopped = name
            print(f"\n== {name} FAILED ==\n{exc}")
            return None

    triggered = await step("Trigger", run_trigger(owner, repo, pr_number))
    diagnosed = None
    triaged = None
    dispatched = None
    if triggered:
        diagnosed = await step(
            "Diagnose",
            run_diagnose(agent, triggered["task_id"], owner, repo, pr_number, triggered["head_sha"]),
        )
    if diagnosed:
        print(json.dumps(
            {
                "missing": (diagnosed["diagnosis"].get("intent_alignment") or {}).get("missing"),
                "files": diagnosed["diagnosis"].get("changed_files"),
                "phantom": diagnosed["diagnosis"].get("is_phantom_pr"),
            },
            indent=2,
        )[:800])
        triaged = await step("Triage", run_triage(agent, triggered["task_id"], diagnosed["diagnosis"]))
    if triaged:
        print(json.dumps(triaged["triage"].get("confidence_scores") or {}, indent=2)[:500])
        dispatched = await step(
            "Dispatch",
            run_dispatch(
                agent,
                triggered["task_id"],
                owner,
                repo,
                pr_number,
                triggered["head_sha"],
                diagnosed["diagnosis"],
                triaged["triage"],
            ),
        )
    if dispatched:
        prompt = dispatched["task"].get("composer_prompt") or ""
        print(prompt)
        print("comment:", dispatched["task"].get("github_comment_url"))
        await step("Prompt quality", run_prompt_quality(prompt))
        launched = await step("Agent launch", run_agent(dispatched["task"]))
        if launched and launched.get("skipped"):
            rows[-1] = ("Agent launch", "SKIPPED", launched["detail"])
            for name in ("Quality gate", "Verify"):
                rows.append((name, "SKIPPED", "no Cursor API key"))
        elif launched:
            print(
                f"branch {launched['status'].get('branch')} pr {launched['status'].get('pr_url')} "
                f"tokens {launched['status'].get('token_usage')}"
            )
            gated = await step(
                "Quality gate",
                run_quality_gate(triggered["task_id"], launched["status"], diagnosed["diagnosis"]),
            )
            if gated:
                print(json.dumps(gated["gate"], indent=2)[:1500])
                await step(
                    "Verify",
                    run_verify(agent, triggered["task_id"], gated["fix"], gated["head_sha"]),
                )

    if stopped:
        rows.append(("OVERALL", "FAIL", f"stopped at {stopped}"))
    else:
        rows.append(("OVERALL", "PASS", ""))
    elapsed = time.perf_counter() - started
    print("\n| Phase | Status | Details |")
    print("|-------|--------|---------|")
    for name, status, detail in rows:
        print(f"| {name} | {status} | {detail.replace('|', '/')} |")
    print(f"\nElapsed: {elapsed:.1f}s")
    print(f"DeepSeek tokens: {LLMClient.total_tokens}")
    print(f"Jev calls: {JevClient.calls}")
    print(f"Estimated cost: {_cost(LLMClient.total_tokens, JevClient.calls)}")
    return 0 if not stopped else 1


if __name__ == "__main__":
    import os

    code = asyncio.run(main())
    os._exit(code)
