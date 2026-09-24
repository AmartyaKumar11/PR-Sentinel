"""Conversational Discord handler. Jev for short commands, DeepSeek for the rest."""

from __future__ import annotations

import json
import logging
import re

from app.agent.prompts import DISCORD_CHAT_PROMPT
from app.config import settings
from app.database import get_db
from app.discord.views import parse_pr_url
from app.services.cursor_client import cursor
from app.services.github_client import GitHubClient
from app.services.task_manager import update_task_status

logger = logging.getLogger(__name__)

# ponytail: ~4 chars/token. Upgrade when a real tokenizer is in the process.
MAX_CONTEXT_CHARS = 8000
DESTRUCTIVE = {"merge", "stop", "close", "reject"}
FAST_ACTIONS = {
    "status": "status",
    "approve": "launch",
    "stop": "stop",
    "merge": "merge",
    "reject": "close",
}
_YES = {"yes", "y", "confirm", "do it", "ship it", "go ahead", "yes do it"}
_NO = {"no", "n", "cancel", "nope", "never mind", "nevermind"}


def should_fast_path(noul: float, text: str) -> bool:
    return noul > 0.85 and len(text.split()) < 15


def allow_action(noul: float) -> bool:
    return noul >= 0.3


def cap_context(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def parse_action(text: str) -> tuple[str, dict | None]:
    decoder = json.JSONDecoder()
    found = None
    span = None
    idx = 0
    while True:
        i = text.find("{", idx)
        if i < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            idx = i + 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("action"), str):
            found = obj
            span = (i, i + end)
        idx = i + 1
    if not found or span is None:
        return text.strip(), None
    cleaned = (text[: span[0]] + text[span[1] :]).strip()
    cleaned = re.sub(r"```(?:json)?\s*```", "", cleaned).strip()
    return cleaned, {"action": found["action"], "params": found.get("params") or {}}


def confirm_line(action: dict, task: dict | None) -> str:
    pr = (task or {}).get("pr_number", "?")
    name = action.get("action")
    if name == "merge":
        return f"Are you sure? This will merge PR #{pr} into main."
    if name in {"close", "reject"}:
        return f"Are you sure? This will close PR #{pr} without merging."
    if name == "stop":
        return "Are you sure? This will stop the running Cursor agent."
    return "Are you sure?"


def _noul(answer) -> float:
    if isinstance(answer, dict):
        return float(answer.get("noul") or 0)
    return float(getattr(answer, "noul", 0) or 0)


def _choice(answer) -> str:
    if isinstance(answer, dict):
        return str(answer.get("choice") or "")
    return str(getattr(answer, "choice", "") or "")


def _loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _summarize(row: dict) -> str:
    diag = _loads(row.get("diagnosis_json"))
    intent = diag.get("intent_alignment") or {}
    blast = diag.get("blast_radius") or {}
    missing = ", ".join(intent.get("missing") or []) or "none"
    fix = (row.get("suggested_fix") or "")[:180]
    return (
        f"PR #{row.get('pr_number')} {row.get('repo')} "
        f"{row.get('severity')} status={row.get('status')}; "
        f"missing: {missing}; risk {blast.get('risk_score', 0)}; "
        f"path {blast.get('highest_risk_path') or 'n/a'}; fix: {fix}"
    )


async def handle_message(bot, message) -> None:
    if message.author.bot or message.guild is None:
        return
    if not settings.DISCORD_CHANNEL_ID.isdigit():
        return
    if message.channel.id != int(settings.DISCORD_CHANNEL_ID):
        return
    text = (message.content or "").strip()
    if not text or text.startswith("/"):
        return
    try:
        async with message.channel.typing():
            reply = await _reply(bot, message, text)
    except Exception:
        logger.exception("discord conversation failed")
        reply = "I couldn't handle that. Try again in a sentence."
    if reply:
        await message.reply(reply[:1900])


async def _reply(bot, message, text: str) -> str:
    if bot._pending_action:
        decision = await _confirm_decision(text)
        if decision is True:
            action = bot._pending_action
            bot._pending_action = None
            return await execute(action)
        if decision is False:
            bot._pending_action = None
            return "Cancelled. Nothing was merged, stopped, or closed."
        bot._pending_action = None

    noul = await _action_noul(text)
    if should_fast_path(noul, text):
        picked = await _fast_choice(text)
        mapped = FAST_ACTIONS.get(picked)
        if mapped:
            action = {"action": mapped, "params": {}}
            if mapped in DESTRUCTIVE:
                bot._pending_action = action
                task = await _latest_task()
                return confirm_line(action, task)
            return await execute(action)

    history = await _history(message)
    system = await _system_prompt()
    from app.services.llm_client import LLMClient

    raw = await LLMClient().chat(system, history)
    prose, action = parse_action(raw)
    if action and not allow_action(noul):
        action = None
    if not action:
        return prose or raw.strip()
    if action["action"] in DESTRUCTIVE:
        bot._pending_action = action
        task = await _latest_task()
        if not re.search(r"are you sure|confirm", prose, re.I):
            prose = (prose + "\n" + confirm_line(action, task)).strip()
        return prose
    result = await execute(action)
    return (prose + "\n" + result).strip() if prose else result


async def _action_noul(text: str) -> float:
    try:
        from typesafe_sdk import Noul

        from app.services.jev_client import JevClient

        answers = await JevClient().evaluate(
            state={"message": text},
            questions={
                "is_direct_action": Noul(
                    instructions=(
                        "Is this message a direct action request "
                        "(merge, stop, approve, reject, status check) "
                        "rather than a question or nuanced instruction?"
                    )
                )
            },
        )
        return _noul(answers["is_direct_action"])
    except Exception:
        logger.warning("jev action gate failed", exc_info=True)
        return 0.5


async def _fast_choice(text: str) -> str:
    try:
        from typesafe_sdk import Choice

        from app.services.jev_client import JevClient

        answers = await JevClient().evaluate(
            state={"message": text},
            questions={
                "action": Choice(
                    instructions="Which single action is the user requesting?",
                    criteria={
                        "status": "Ask how the agent or task is doing",
                        "approve": "Approve the fix or start the cloud agent",
                        "stop": "Cancel the running agent",
                        "merge": "Merge the pull request",
                        "reject": "Reject or close the fix",
                    },
                )
            },
        )
        return _choice(answers["action"])
    except Exception:
        logger.warning("jev fast choice failed", exc_info=True)
        return ""


async def _confirm_decision(text: str) -> bool | None:
    folded = text.lower().strip()
    if folded in _YES:
        return True
    if folded in _NO:
        return False
    try:
        from typesafe_sdk import Noul

        from app.services.jev_client import JevClient

        answers = await JevClient().evaluate(
            state={"message": text},
            questions={
                "confirms": Noul(
                    instructions="The user is confirming they want the pending destructive action to proceed"
                ),
                "cancels": Noul(
                    instructions="The user is cancelling the pending destructive action"
                ),
            },
        )
        if _noul(answers["confirms"]) > 0.7:
            return True
        if _noul(answers["cancels"]) > 0.7:
            return False
    except Exception:
        logger.warning("jev confirm check failed", exc_info=True)
    return None


async def _history(message) -> list[dict]:
    rows = []
    async for msg in message.channel.history(limit=10):
        content = (msg.content or "").strip()
        if not content:
            continue
        role = "assistant" if msg.author.bot else "user"
        rows.append({"role": role, "content": content[:500]})
    rows.reverse()
    blob = rows
    while blob and sum(len(m["content"]) for m in blob) > 4000:
        blob.pop(0)
    return blob[-10:]


async def _system_prompt() -> str:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, repo, pr_number, severity, status, suggested_fix, "
        "diagnosis_json, cursor_agent_id FROM tasks ORDER BY created_at DESC LIMIT 3"
    )
    tasks = [dict(r) for r in await cur.fetchall()]
    task_state = "\n".join(_summarize(t) for t in tasks) or "No tasks yet."
    cursor_state = "idle"
    latest = tasks[0] if tasks else None
    agent_id = (latest or {}).get("cursor_agent_id")
    if agent_id:
        try:
            status = await cursor.get_run_status(agent_id)
            cursor_state = (
                f"status={status.get('status')} branch={status.get('branch') or 'n/a'} "
                f"pr={status.get('pr_url') or 'none'} model={cursor.default_model}"
            )
        except Exception:
            logger.warning("cursor status for chat context failed", exc_info=True)
            cursor_state = f"agent {agent_id} (status unavailable) model={cursor.default_model}"
    else:
        cursor_state = f"idle model={cursor.default_model}"
    prompt = DISCORD_CHAT_PROMPT.replace("{task_state}", task_state).replace(
        "{cursor_state}", cursor_state
    )
    return cap_context(prompt)


async def _latest_task() -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tasks WHERE status NOT IN ('resolved','error','dismissed') "
        "ORDER BY created_at DESC LIMIT 1"
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def execute(action: dict) -> str:
    name = action.get("action") or ""
    params = action.get("params") or {}
    task = await _latest_task()
    if name == "defer":
        return "Holding off. The task stays where it is."
    if name == "status":
        return await _status_text(task)
    if name == "model":
        model = params.get("name") or params.get("model")
        if not model:
            return "Which model should I use?"
        cursor.default_model = model
        return f"Next run will use `{model}`."
    if name in {"launch", "approve"}:
        return await _launch(task, params)
    if name == "resume":
        return await _resume(task, params.get("message") or params.get("prompt") or "")
    if name == "stop":
        if not task or not task.get("cursor_agent_id"):
            return "No running agent to stop."
        await cursor.cancel_agent(task["cursor_agent_id"])
        return "Agent stopped."
    if name == "merge":
        return await _merge(task, params.get("pr_url"))
    if name in {"close", "reject"}:
        return await _close(task, params.get("pr_url"))
    if name == "diff":
        return await _diff(task, params.get("pr_url"))
    if name == "logs":
        return await _logs(task)
    return f"I don't have a way to run `{name}` yet."


async def _status_text(task: dict | None) -> str:
    if not task:
        return "No active task."
    line = f"PR #{task['pr_number']} {task['repo']} — {task['severity']} / {task['status']}."
    if task.get("cursor_agent_id"):
        try:
            status = await cursor.get_run_status(task["cursor_agent_id"])
            line += (
                f" Agent {status.get('status')}."
                f" Branch {status.get('branch') or 'n/a'}."
                f" PR {status.get('pr_url') or 'not yet'}."
            )
        except Exception:
            line += " Agent status unavailable."
    return line


async def _launch(task: dict | None, params: dict) -> str:
    if not task:
        return "No task to launch against."
    prompt = params.get("prompt") or task.get("composer_prompt") or task.get("suggested_fix") or ""
    result = await cursor.launch_agent(
        repo_full_name=task["repo"],
        prompt=prompt,
        model=params.get("model"),
        branch=params.get("branch") or f"pr-sentinel/fix-{task['pr_number']}",
    )
    db = await get_db()
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
        (result["agent_id"], task["id"]),
    )
    await db.commit()
    try:
        await update_task_status(db, task["id"], "accepted")
    except ValueError:
        pass
    return f"Launched `{result['agent_id']}` with {result['model']}."


async def _resume(task: dict | None, message: str) -> str:
    if not task or not task.get("cursor_agent_id"):
        return "No agent to resume. I can launch one instead."
    result = await cursor.resume_agent(task["cursor_agent_id"], message or None)
    return f"Sent that to the agent. Status: {result.get('status')}."


async def _pr_target(task: dict | None, pr_url: str | None):
    parsed = parse_pr_url(pr_url or "")
    if parsed:
        return parsed
    if not task or not task.get("cursor_agent_id"):
        return None
    status = await cursor.get_run_status(task["cursor_agent_id"])
    return parse_pr_url(status.get("pr_url") or "")


async def _merge(task: dict | None, pr_url: str | None) -> str:
    target = await _pr_target(task, pr_url)
    if not target:
        return "No pull request to merge yet."
    result = await GitHubClient().merge_pr(*target)
    if result.get("merged"):
        return f"Merged PR #{target[2]}."
    return f"Merge failed: {result.get('message')}"


async def _close(task: dict | None, pr_url: str | None) -> str:
    target = await _pr_target(task, pr_url)
    if target:
        await GitHubClient().close_pr(*target)
    if task:
        try:
            await update_task_status(await get_db(), task["id"], "dismissed")
        except ValueError:
            pass
    if not target:
        return "No fix PR to close. Task left as-is." if not task else "Task dismissed. No fix PR was open."
    return f"Closed PR #{target[2]}."


async def _diff(task: dict | None, pr_url: str | None) -> str:
    target = await _pr_target(task, pr_url)
    if not target:
        return "No pull request diff yet."
    diff = await GitHubClient().get_pr_diff(*target)
    if len(diff) > 1700:
        diff = diff[:1700] + "\n... (truncated)"
    return f"```diff\n{diff}\n```"


async def _logs(task: dict | None) -> str:
    if not task:
        return "No task to show logs for."
    db = await get_db()
    cur = await db.execute(
        "SELECT phase, type, content FROM trace_steps WHERE task_id = ? ORDER BY id",
        (task["id"],),
    )
    lines = []
    for row in (await cur.fetchall())[:15]:
        row = dict(row)
        lines.append(f"[{row['phase']}] {row['content'][:100]}")
    return "\n".join(lines) or "No trace steps recorded."
