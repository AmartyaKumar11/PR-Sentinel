"""Phased agent: DIAGNOSE → Jev TRIAGE → DISPATCH / VERIFY."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.agent.composer_prompt import format_review_markdown, generate_composer_prompt
from app.agent.parser import parse_agent_output
from app.agent.tools.setup import build_tool_registry
from app.config import settings
from app.database import get_db
from app.services.diff_parser import extract_changed_identifiers, is_trivial_diff
from app.services.jev_client import JevClient
from app.services.jev_triage import (
    assemble_triage,
    assemble_verification,
    build_triage_questions,
    build_verify_questions,
    trivial_check_questions,
)
from app.services.llm_client import LLMClient
from app.services.sse_manager import sse_manager
from app.services.task_manager import (
    create_task,
    get_task,
    save_trace_step,
    update_task_status,
)

logger = logging.getLogger(__name__)


def _truncate(s: str, max_chars: int = 6000) -> str:
    return s if len(s) <= max_chars else s[:max_chars] + "\n...truncated..."


def _reqs_from_issue(issue: dict | None) -> list[str]:
    if not issue:
        return []
    body = issue.get("body") or ""
    bullets = re.findall(r"^[\-\*]\s+(.+)$", body, re.MULTILINE)
    if bullets:
        return [b.strip() for b in bullets if len(b.strip()) > 3][:12]
    # sentences as weak requirements
    parts = [p.strip() for p in re.split(r"[.\n]", body) if len(p.strip()) > 15]
    return parts[:5]


class AgentOrchestrator:
    def __init__(self, deepseek: LLMClient | None = None, jev: JevClient | None = None):
        self.deepseek = deepseek or LLMClient()
        self.jev = jev or JevClient()
        self.sse = sse_manager
        # ponytail: trace_steps FK requires the task row, which dispatch creates.
        # Buffer per task_id until then. Upgrade when the webhook inserts the row first.
        self._pending_traces: dict[str, list] = {}

    async def run(
        self,
        task_id: str,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        mode: str = "full",
    ):
        try:
            if mode == "full":
                diagnosis = await self._run_diagnose(task_id, owner, repo, pr_number, head_sha)
                if diagnosis.get("is_trivial"):
                    triage = {
                        "severity": "TRIVIAL",
                        "action": "skip",
                        "justification": "Trivial non-code PR",
                        "suggested_fix_approach": "",
                        "affected_files_priority": [],
                        "intent_alignment": diagnosis.get("intent_alignment")
                        or {"addressed": [], "missing": [], "scope_creep": []},
                        "confidence_scores": {"is_trivial": 1.0},
                    }
                else:
                    triage = await self._run_jev_triage(task_id, diagnosis)
                await self._run_dispatch(
                    task_id, owner, repo, pr_number, head_sha, diagnosis, triage
                )
            elif mode == "verify":
                await self._run_jev_verify(task_id, owner, repo, pr_number, head_sha)
        except Exception as e:
            logger.exception("agent failed task=%s", task_id)
            await self._emit_and_persist(
                task_id,
                {"step": 0, "phase": "diagnose", "type": "error", "content": str(e)},
            )
            db = await get_db()
            try:
                await update_task_status(db, task_id, "error")
            except Exception:
                pass

    async def _emit(self, task_id, step, phase, type_, content, **extra):
        event = {"step": step, "phase": phase, "type": type_, "content": content, **extra}
        await self._emit_and_persist(task_id, event)

    async def _emit_and_persist(self, task_id, event):
        self.sse.emit(task_id, event)
        content = event.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content)
        row = (
            event.get("step", 0),
            event.get("phase") or "diagnose",
            event.get("type") or "thought",
            content[:2000],
            event.get("tool"),
            event.get("args"),
            event.get("elapsed_ms"),
        )
        db = await get_db()
        if await get_task(db, task_id):
            await self._flush_traces(task_id)
            await save_trace_step(db, task_id, *row)
        else:
            self._pending_traces.setdefault(task_id, []).append(row)

    async def _flush_traces(self, task_id):
        pending = self._pending_traces.pop(task_id, [])
        if not pending:
            return
        db = await get_db()
        for row in pending:
            await save_trace_step(db, task_id, *row)

    async def _run_diagnose(self, task_id, owner, repo, pr_number, head_sha) -> dict:
        tools = build_tool_registry(owner, repo)
        step = 0

        await self._emit(task_id, step, "diagnose", "thought", "Fetching PR diff")
        step += 1
        await self._emit(
            task_id, step, "diagnose", "action", f"fetch_pr_diff({pr_number})", tool="fetch_pr_diff", args={"pr_number": pr_number}
        )
        diff_result = await tools.execute("fetch_pr_diff", pr_number=pr_number)
        raw_diff = diff_result.get("diff", "") if isinstance(diff_result, dict) else str(diff_result)
        files = diff_result.get("files", []) if isinstance(diff_result, dict) else []
        await self._emit(task_id, step, "diagnose", "observation", _truncate(raw_diff, 500))

        # Jev fast-exit
        step += 1
        await self._emit(task_id, step, "diagnose", "thought", "Jev trivial check")
        try:
            trivial = await self.jev.evaluate(
                state={"diff": _truncate(raw_diff, 4000)},
                questions=trivial_check_questions(),
            )
            is_triv = trivial["is_trivial"].noul > 0.9
            skip_graph = trivial["touches_functions"].noul < 0.2
        except Exception as e:
            logger.warning("jev trivial check failed: %s", e)
            is_triv = is_trivial_diff(raw_diff)
            skip_graph = is_triv

        if is_triv:
            diagnosis = {
                "is_trivial": True,
                "changed_files": files,
                "changed_identifiers": [],
                "linked_issue": None,
                "is_phantom_pr": False,
                "is_underspecified_issue": False,
                "diff_summary": _truncate(raw_diff, 2000),
                "intent_alignment": {"addressed": [], "missing": [], "scope_creep": []},
                "blast_radius": {
                    "directly_changed": [],
                    "depth_1_impacted": [],
                    "depth_2_impacted": [],
                    "highest_risk_path": "",
                    "risk_score": 0.0,
                    "untested_impacted": [],
                },
            }
            await self._emit(task_id, step, "diagnose", "answer", json.dumps(diagnosis))
            return diagnosis

        step += 1
        await self._emit(task_id, step, "diagnose", "action", "fetch_linked_issue", tool="fetch_linked_issue")
        issue = await tools.execute("fetch_linked_issue", pr_number=pr_number)
        if isinstance(issue, dict) and issue.get("error"):
            issue = None
        await self._emit(task_id, step, "diagnose", "observation", json.dumps(issue)[:500] if issue else "null")

        identifiers = extract_changed_identifiers(raw_diff)
        blast = {
            "directly_changed": identifiers,
            "depth_1_impacted": [],
            "depth_2_impacted": [],
            "highest_risk_path": "",
            "risk_score": 0.0,
            "untested_impacted": [],
        }
        if not skip_graph and identifiers:
            step += 1
            await self._emit(task_id, step, "diagnose", "action", "build_dependency_graph", tool="build_dependency_graph")
            graph = await tools.execute("build_dependency_graph", ref=head_sha, path_filter="src/")
            step += 1
            await self._emit(task_id, step, "diagnose", "action", "trace_blast_radius", tool="trace_blast_radius")
            blast = await tools.execute(
                "trace_blast_radius", changed_identifiers=identifiers, dep_graph=graph
            )

        reqs = _reqs_from_issue(issue)
        phantom = issue is None
        diagnosis = {
            "is_trivial": False,
            "changed_files": files,
            "changed_identifiers": identifiers,
            "linked_issue": (
                {
                    "number": issue["number"],
                    "title": issue.get("title", ""),
                    "body": issue.get("body", ""),
                    "requirements": reqs,
                }
                if issue
                else None
            ),
            "is_phantom_pr": phantom,
            "is_underspecified_issue": bool(issue) and len(reqs) < 2,
            "diff_summary": _truncate(raw_diff, 2000),
            "intent_alignment": {
                "addressed": [],
                "missing": [],
                "scope_creep": files if phantom else [],
            },
            "blast_radius": blast,
        }
        # DeepSeek judges whether the diff implements each requirement.
        if issue and reqs:
            step += 1
            await self._emit(task_id, step, "diagnose", "thought", "DeepSeek intent alignment")
            try:
                msg = await self.deepseek.chat(
                    system=(
                        "You are analyzing a PR diff against issue requirements. "
                        "For each requirement, determine if the diff ACTUALLY IMPLEMENTS it — "
                        "not just mentions related words. A function that takes an email parameter "
                        "does NOT implement email validation unless it contains validation logic "
                        "(regex, format check, library call). A comment that only says validation is missing does not count. Be strict. "
                        "Copy each requirement string exactly into addressed or missing. "
                        "Return only JSON."
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Requirements:\n"
                                + "\n".join(f"- {r}" for r in reqs)
                                + f"\nChanged files: {files}\n"
                                f"Diff:\n{_truncate(raw_diff, 3000)}\n"
                                'Return JSON: {"addressed":[],"missing":[],"scope_creep":[]}'
                            ),
                        }
                    ],
                )
                parsed = parse_agent_output(msg)
                if parsed.type == "answer":
                    intent = json.loads(parsed.content)
                    diagnosis["intent_alignment"].update(
                        {
                            "addressed": intent.get("addressed") or [],
                            "missing": intent.get("missing") or [],
                            "scope_creep": intent.get("scope_creep") or [],
                        }
                    )
            except Exception as e:
                logger.warning("intent LLM failed: %s", e)

        step += 1
        await self._emit(task_id, step, "diagnose", "answer", json.dumps(diagnosis)[:2000])
        return diagnosis

    async def _run_jev_triage(self, task_id, diagnosis) -> dict:
        questions = build_triage_questions(diagnosis)
        br = diagnosis.get("blast_radius") or {}
        intent = diagnosis.get("intent_alignment") or {}
        missing = intent.get("missing") or []
        scope = intent.get("scope_creep") or []
        state = {
            "diff_summary": diagnosis.get("diff_summary", ""),
            "issue_title": (diagnosis.get("linked_issue") or {}).get("title", ""),
            "issue_body": (diagnosis.get("linked_issue") or {}).get("body", ""),
            "changed_files": diagnosis.get("changed_files") or [],
            "missing_count": len(missing),
            "has_scope_creep": len(scope) > 0,
            "has_missing_requirements": len(missing) > 0,
            "blast_radius": {
                "impacted_count": len(br.get("depth_1_impacted") or [])
                + len(br.get("depth_2_impacted") or []),
                "untested_count": len(br.get("untested_impacted") or []),
                "highest_risk_path": br.get("highest_risk_path", ""),
            },
        }
        answers = await self.jev.evaluate(state=state, questions=questions)
        triage = assemble_triage(diagnosis, answers)

        # Narrative via DeepSeek
        try:
            narrative = await self.deepseek.chat(
                system="You are a code review assistant. Reply with Answer: JSON only.",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Missing: {triage['intent_alignment'].get('missing')}\n"
                            f"Scope: {triage['intent_alignment'].get('scope_creep')}\n"
                            f"Files: {diagnosis.get('changed_files')}\n"
                            'Answer: {"suggested_fix_approach":"...","affected_files_priority":[{"path":"...","lines":[],"change_type":"modified"}]}'
                        ),
                    }
                ],
            )
            parsed = parse_agent_output(narrative)
            if parsed.type == "answer":
                data = json.loads(parsed.content)
                triage["suggested_fix_approach"] = data.get("suggested_fix_approach", "")
                triage["affected_files_priority"] = data.get("affected_files_priority") or []
        except Exception as e:
            logger.warning("narrative LLM failed: %s", e)
            triage["suggested_fix_approach"] = triage.get("justification", "")
            triage["affected_files_priority"] = [
                {"path": f, "lines": [], "change_type": "modified"}
                for f in (diagnosis.get("changed_files") or [])[:5]
            ]

        await self._emit_and_persist(
            task_id,
            {
                "step": 0,
                "phase": "triage",
                "type": "answer",
                "content": json.dumps(
                    {"severity": triage["severity"], "action": triage["action"]}
                ),
            },
        )
        return triage

    async def _run_dispatch(
        self, task_id, owner, repo, pr_number, head_sha, diagnosis, triage
    ):
        # Sync intent onto diagnosis for templates
        if triage.get("intent_alignment"):
            diagnosis["intent_alignment"] = {
                **diagnosis.get("intent_alignment", {}),
                **triage["intent_alignment"],
            }

        composer = generate_composer_prompt(diagnosis, triage)
        try:
            from app.services.prompt_crafter import craft_prompt

            composer = await craft_prompt(diagnosis, triage, deepseek=self.deepseek)
        except Exception:
            logger.warning("prompt craft failed", exc_info=True)
        review_md = format_review_markdown(diagnosis, triage)
        db = await get_db()
        await create_task(
            db,
            task_id,
            f"{owner}/{repo}",
            pr_number,
            head_sha,
            triage["severity"],
            triage["action"],
            diagnosis,
            triage,
            review_md,
            composer,
        )
        await self._flush_traces(task_id)

        if triage.get("action") != "skip":
            tools = build_tool_registry(owner, repo)
            result = await tools.execute(
                "post_pr_review", pr_number=pr_number, review_body=review_md
            )
            if isinstance(result, dict) and not result.get("error"):
                await update_task_status(
                    db,
                    task_id,
                    "dispatched",
                    github_comment_id=result.get("id"),
                    github_comment_url=result.get("html_url"),
                )
            else:
                await update_task_status(db, task_id, "dispatched")

        await self._emit_and_persist(
            task_id,
            {"step": 0, "phase": "dispatch", "type": "answer", "content": "Task dispatched."},
        )
        await self._notify_discord(task_id, owner, repo, pr_number, diagnosis, triage, composer)

    async def _run_jev_verify(self, task_id, owner, repo, pr_number, head_sha):
        db = await get_db()
        task = await get_task(db, task_id)
        if not task:
            return
        diagnosis = task.get("diagnosis_json")
        if isinstance(diagnosis, str):
            diagnosis = json.loads(diagnosis)
        intent = (diagnosis or {}).get("intent_alignment") or {}
        prev_missing = intent.get("missing") or []
        prev_scope = intent.get("scope_creep") or []

        tools = build_tool_registry(owner, repo)
        diff_result = await tools.execute("fetch_pr_diff", pr_number=pr_number)
        raw = diff_result.get("diff", "") if isinstance(diff_result, dict) else ""

        questions = build_verify_questions(prev_missing, prev_scope)
        answers = await self.jev.evaluate(
            state={
                "new_diff": _truncate(raw, 4000),
                "original_issue": (diagnosis or {}).get("linked_issue") or {},
                "previously_missing": prev_missing,
                "previously_flagged_scope_creep": prev_scope,
            },
            questions=questions,
        )
        verification = assemble_verification(prev_missing, prev_scope, answers)

        if verification["all_resolved"]:
            await update_task_status(
                db,
                task_id,
                "resolved",
                resolved_sha=head_sha,
                verification_json=json.dumps(verification),
                is_verified=1,
            )
            await tools.execute(
                "post_pr_review",
                pr_number=pr_number,
                review_body="PR Sentinel: All issues addressed.",
            )
        else:
            await update_task_status(
                db, task_id, "dispatched", verification_json=json.dumps(verification)
            )
            n = len(verification["remaining_items"])
            await tools.execute(
                "post_pr_review",
                pr_number=pr_number,
                review_body=f"PR Sentinel: {n} issues remain.",
            )
        await self._emit_and_persist(
            task_id,
            {
                "step": 0,
                "phase": "verify",
                "type": "answer",
                "content": json.dumps(verification),
            },
        )
        try:
            from app.discord.bot import bot

            if bot.is_ready():
                await bot.send_verification_result(task_id, verification)
        except Exception:
            logger.warning("discord verify notify failed", exc_info=True)

    async def _notify_discord(self, task_id, owner, repo, pr_number, diagnosis, triage, composer):
        auto = False
        if settings.CURSOR_API_KEY:
            try:
                from typesafe_sdk import Noul

                missing = (diagnosis.get("intent_alignment") or {}).get("missing") or []
                answers = await self.jev.evaluate(
                    state={
                        "severity": triage.get("severity"),
                        "risk_score": (diagnosis.get("blast_radius") or {}).get("risk_score", 0),
                        "missing_count": len(missing),
                    },
                    questions={
                        "auto_approvable": Noul(
                            instructions=(
                                "This fix is low-risk enough to auto-approve without human confirmation. "
                                "Only true for LOW severity with risk_score under 0.2 and no missing requirements."
                            )
                        )
                    },
                )
                auto = answers["auto_approvable"].noul > 0.85
            except Exception:
                logger.warning("auto-approve check failed", exc_info=True)
        if auto and triage.get("action") != "skip":
            from app.services.cursor_client import cursor

            result = await cursor.launch_agent(
                repo_full_name=f"{owner}/{repo}",
                prompt=composer,
                branch=f"pr-sentinel/fix-{pr_number}",
            )
            db = await get_db()
            await db.execute(
                "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
                (result["agent_id"], task_id),
            )
            await db.commit()
            await update_task_status(db, task_id, "accepted")
            from app.discord.bot import bot
            from app.discord.views import monitor_agent

            if bot.channel is not None:
                asyncio.create_task(monitor_agent(result["agent_id"], bot.channel, task_id))
            return
        try:
            from app.discord.bot import bot

            if not bot.is_ready():
                return
            await bot.send_task_notification(
                {
                    "id": task_id,
                    "repo": f"{owner}/{repo}",
                    "pr_number": pr_number,
                    "severity": triage["severity"],
                    "suggested_fix": triage.get("suggested_fix_approach", ""),
                    "diagnosis_json": json.dumps(diagnosis),
                    "jev_confidences": json.dumps(triage.get("confidence_scores") or {}),
                }
            )
        except Exception:
            logger.warning("discord notify failed", exc_info=True)
