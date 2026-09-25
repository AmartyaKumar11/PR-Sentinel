"""Conversational Discord handler. Jev for short commands, DeepSeek for the rest."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from app.agent.prompts import DISCORD_AGENT_PROMPT
from app.config import settings
from app.database import get_db
from app.services.cursor_client import cursor, format_status_reply, model_label
from app.discord.views import merge_with_conflict_resolution
from app.services.github_client import GitHubClient
from app.services.task_manager import accept_task, get_health_stats, update_task_status

logger = logging.getLogger(__name__)

# ponytail: ~4 chars/token. Dynamic context only; the system prompt is separate.
MAX_CONTEXT_CHARS = 8000
_ACTIVE = "('resolved','error','dismissed')"
_MODELS = (
    "cursor-small, gpt-4o-mini, gpt-4o, claude-sonnet-4-20250514, "
    "gemini-2.5-pro, claude-opus-4-20250514"
)
_FENCE = re.compile(r"```action\s*\n(.*?)\n?```", re.S)
FAST_ACTIONS = {
    "status": "get_status",
    "approve": "launch_agent",
    "stop": "stop_agent",
    "merge": "merge_pr",
    "reject": "close_pr",
}
_FAST_CONFIRM = {"merge_pr", "close_pr"}
_YES = {"yes", "y", "confirm", "do it", "ship it", "go ahead", "yes do it", "yes merge"}
_NO = {"no", "n", "cancel", "nope", "never mind", "nevermind"}
_TIMEOUT_REPLY = (
    "Taking too long to think. Try a simpler phrasing "
    "or use a slash command: /status, /merge, /stop"
)


def should_fast_path(noul: float, text: str) -> bool:
    return noul > 0.85 and len(text.split()) < 15


def allow_action(noul: float) -> bool:
    return noul >= 0.3


def parse_action(text: str) -> tuple[str, dict | None]:
    match = _FENCE.search(text or "")
    if not match:
        return (text or "").strip(), None
    prose = (text[: match.start()] + text[match.end() :]).strip()
    try:
        obj = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        logger.warning("discord action block was not valid JSON")
        return prose, None
    if not isinstance(obj, dict) or not isinstance(obj.get("action"), str):
        logger.warning("discord action block missing action")
        return prose, None
    return prose, {"action": obj["action"], "params": obj.get("params") or {}}


def format_active(tasks: list[dict], detailed: bool) -> str:
    if not tasks:
        return "none"
    lines = []
    for task in tasks:
        if not detailed:
            lines.append(f"PR #{task['pr_number']} — {task['severity']} — {task['status']}")
            continue
        lines.append(
            f"PR #{task['pr_number']} — {task['repo']} — {task['severity']} — {task['status']}\n"
            f"Missing: {task.get('missing') or 'none'}\n"
            f"Agent: {task.get('agent') or 'none'}"
        )
    return "\n".join(lines)


def render_context(active, agent_block, recent, convo, *, detailed=True, include_recent=True) -> str:
    parts = [
        "CURRENT STATE:",
        "",
        "Active tasks:",
        format_active(active, detailed),
        "",
        agent_block.strip(),
        "",
    ]
    if include_recent:
        parts += ["Recent history:", "\n".join(recent) if recent else "none", ""]
    parts += ["Conversation so far:", "\n".join(convo) if convo else "none"]
    return "\n".join(parts).strip()


def fit_context(active, agent_block, recent, convo, limit: int = MAX_CONTEXT_CHARS) -> str:
    text = render_context(active, agent_block, recent, convo)
    if len(text) <= limit:
        return text
    text = render_context(active, agent_block, recent, convo, include_recent=False)
    if len(text) <= limit:
        return text
    text = render_context(active, agent_block, recent, convo, detailed=False, include_recent=False)
    return text if len(text) <= limit else text[:limit]


def confirm_line(action: dict, task: dict | None) -> str:
    pr = (task or {}).get("pr_number", "?")
    name = action.get("action")
    if name == "merge_pr":
        return f"Are you sure? This will merge PR #{pr} into main."
    if name == "close_pr":
        return f"Are you sure? This will close PR #{pr} without merging."
    if name == "stop_agent":
        return "Are you sure? This will stop the running Cursor agent."
    if name == "dismiss_task":
        return f"Are you sure? This will dismiss PR #{pr}."
    if name == "resolve_task":
        return f"Are you sure? This will mark PR #{pr} resolved."
    if name == "relaunch_agent":
        return "Are you sure? This will stop the running agent and start a new one."
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


def _split_repo(full: str) -> tuple[str, str]:
    if "/" not in (full or ""):
        raise ValueError(f"Repo must be owner/name, got {full!r}")
    owner, repo = full.split("/", 1)
    return owner, repo


def _clip(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n(truncated)"


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
    except Exception as exc:
        logger.exception("discord conversation failed")
        reply = f"Tried to answer that.\n\n⚠️ Action failed: {exc}"
    if reply:
        await message.reply(reply[:1900])


async def _reply(bot, message, text: str) -> str:
    if bot._pending_action:
        decision = await _confirm_decision(text)
        if decision is True:
            action = bot._pending_action
            bot._pending_action = None
            return await _run(action, channel=message.channel)
        if decision is False:
            bot._pending_action = None
            return "Cancelled. Nothing was merged, stopped, or closed."
        bot._pending_action = None

    noul = await _action_noul(text)
    if should_fast_path(noul, text):
        mapped = FAST_ACTIONS.get(await _fast_choice(text))
        if mapped:
            action = {"action": mapped, "params": {}}
            if mapped in _FAST_CONFIRM:
                bot._pending_action = action
                return confirm_line(action, await _latest_task())
            return await _run(action, channel=message.channel)

    context = await build_dynamic_context(message)
    try:
        raw = await asyncio.wait_for(
            _chat(context, text),
            timeout=15,
        )
    except TimeoutError:
        logger.warning("deepseek discord chat timed out")
        return _TIMEOUT_REPLY

    prose, action = parse_action(raw)
    if action and not allow_action(noul):
        logger.info("dropped discord action; jev noul %.2f", noul)
        action = None
    if not action:
        return prose or raw.strip()
    result = await _run(action, prose, channel=message.channel)
    return result


async def _chat(context: str, text: str) -> str:
    from app.services.llm_client import LLMClient

    return await LLMClient().chat(
        DISCORD_AGENT_PROMPT,
        [
            {"role": "user", "content": context},
            {"role": "user", "content": text},
        ],
    )


async def _run(action: dict, prose: str = "", channel=None) -> str:
    try:
        result = await execute(action, channel=channel)
    except Exception as exc:
        logger.exception("discord action %s failed", action.get("action"))
        fail = f"⚠️ Action failed: {exc}"
        return f"{prose}\n\n{fail}".strip() if prose else fail
    if not prose:
        return result
    return f"{prose}\n{result}".strip()


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


async def build_dynamic_context(message) -> str:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, repo, pr_number, severity, status, diagnosis_json, cursor_agent_id "
        f"FROM tasks WHERE status NOT IN {_ACTIVE} ORDER BY created_at DESC LIMIT 5"
    )
    active = []
    for row in await cur.fetchall():
        row = dict(row)
        missing = (_loads(row.get("diagnosis_json")).get("intent_alignment") or {}).get("missing") or []
        active.append(
            {
                "pr_number": row["pr_number"],
                "repo": row["repo"],
                "severity": row["severity"],
                "status": row["status"],
                "missing": ", ".join(missing) if missing else "none",
                "agent": row.get("cursor_agent_id") or "none",
                "cursor_agent_id": row.get("cursor_agent_id"),
            }
        )
    hist = await db.execute(
        "SELECT pr_number, severity, resolved_at FROM tasks WHERE status = 'resolved' "
        "ORDER BY resolved_at DESC LIMIT 3"
    )
    recent = [
        f"PR #{row['pr_number']} — {row['severity']} — resolved {row['resolved_at']}"
        for row in await hist.fetchall()
    ]
    agent_task = next((t for t in active if t.get("cursor_agent_id")), None)
    if agent_task:
        agent_id = agent_task["cursor_agent_id"]
        try:
            status = await cursor.get_run_status(agent_id)
            agent_block = (
                "Cursor agent:\n"
                f"{format_status_reply(agent_id, status)}\n"
                f"Model: {model_label(cursor.default_model)}"
            )
        except Exception as exc:
            logger.warning("cursor status for chat context failed", exc_info=True)
            agent_block = (
                "Cursor agent:\n"
                f"Agent ID: {agent_id}\n"
                f"Status: unavailable ({exc})\n"
                f"Model: {model_label(cursor.default_model)}\n"
                "Branch: n/a\n"
                "PR URL: none"
            )
    else:
        agent_block = (
            "No active agent:\n"
            f"Default model: {model_label(cursor.default_model)}\n"
            f"Available models: {_MODELS}"
        )
    convo = []
    async for msg in message.channel.history(limit=10):
        content = (msg.content or "").strip()
        if content:
            who = "bot" if msg.author.bot else "user"
            convo.append(f"{who}: {content[:400]}")
    convo.reverse()
    return fit_context(active, agent_block, recent, convo)


async def _latest_task() -> dict | None:
    db = await get_db()
    cur = await db.execute(
        f"SELECT * FROM tasks WHERE status NOT IN {_ACTIVE} ORDER BY created_at DESC LIMIT 1"
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def _pick_task(params: dict) -> dict | None:
    db = await get_db()
    if params.get("task_id"):
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (params["task_id"],))
    elif params.get("pr_number"):
        cur = await db.execute(
            "SELECT * FROM tasks WHERE pr_number = ? ORDER BY created_at DESC LIMIT 1",
            (int(params["pr_number"]),),
        )
    else:
        return await _latest_task()
    row = await cur.fetchone()
    return dict(row) if row else None


def _target(params: dict, task: dict | None) -> tuple[str, str, int]:
    if params.get("owner") and params.get("repo") and params.get("pr_number"):
        return params["owner"], params["repo"], int(params["pr_number"])
    if not task:
        raise ValueError("No pull request to act on.")
    owner, repo = _split_repo(task["repo"])
    return owner, repo, int(params.get("pr_number") or task["pr_number"])


async def execute(action: dict, channel=None) -> str:
    name = action.get("action") or ""
    params = action.get("params") or {}
    task = await _pick_task(params)
    if name == "get_status":
        return await _status_text(task)
    if name == "set_model":
        model = params.get("model")
        if not model:
            return "Which model should I use?"
        cursor.default_model = model
        return f"Next run will use `{model_label(model)}`."
    if name == "list_models":
        models = await cursor.list_models()
        return ", ".join(f"`{m}`" for m in models) or "No models returned."
    if name == "launch_agent":
        return await _launch(task, params, channel)
    if name == "relaunch_agent":
        if task and await _agent_running(task):
            return "Agent is already running — use /status to check progress."
        if task and task.get("cursor_agent_id"):
            await cursor.cancel_agent(task["cursor_agent_id"])
        return await _launch(task, params, channel)
    if name == "resume_agent":
        return await _resume(task, params.get("message") or "")
    if name == "stop_agent":
        return await _stop(task)
    if name == "merge_pr":
        owner, repo, number = _target(params, task)
        result = await merge_with_conflict_resolution(
            owner, repo, number, channel, params.get("method") or "squash"
        )
        if result.get("announced"):
            return ""
        if result.get("merged"):
            return f"Merged PR #{number}."
        return f"Merge failed: {result.get('message')}"
    if name == "close_pr":
        owner, repo, number = _target(params, task)
        result = await GitHubClient().close_pr(owner, repo, number)
        if not result.get("closed", True):
            return f"Close failed: {result.get('message')}"
        return f"Closed PR #{number}."
    if name == "post_comment":
        owner, repo, number = _target(params, task)
        body = params.get("body") or ""
        if not body:
            return "What should the comment say?"
        posted = await GitHubClient().post_comment(owner, repo, number, body)
        return f"Comment posted: {posted.get('html_url')}"
    if name == "dismiss_task":
        return await _set_status(task, "dismissed", "Task dismissed.")
    if name == "resolve_task":
        return await _set_status(task, "resolved", "Marked resolved.")
    if name == "reopen_task":
        return await _set_status(task, "dispatched", "Reopened.")
    if name == "rediagnose":
        return await _rediagnose(task, params)
    if name == "get_diff":
        owner, repo, number = _target(params, task)
        diff = await GitHubClient().get_pr_diff(owner, repo, number)
        return f"```diff\n{_clip(diff)}\n```"
    if name == "get_trace":
        return await _trace(task)
    if name == "get_tasks":
        return await _tasks(params)
    if name == "get_prompt":
        if not task:
            return "No task to show a prompt for."
        return f"```\n{_clip(task.get('composer_prompt') or 'No Composer prompt stored.')}\n```"
    if name == "get_attempts":
        return _attempts_text(task)
    if name == "get_health":
        stats = await get_health_stats(await get_db())
        return (
            f"Backend ok. Reviews {stats['reviews_completed']}. "
            f"Last event {stats['last_webhook_at']}. Model `{settings.LLM_MODEL}`."
        )
    if name == "update_prompt":
        return await _write_prompt(task, params.get("new_prompt") or "", replace=True)
    if name == "append_to_prompt":
        return await _write_prompt(task, params.get("addition") or "", replace=False)
    return f"No handler for `{name}`."


async def _set_status(task: dict | None, status: str, ok: str) -> str:
    if not task:
        return "No task for that."
    await update_task_status(await get_db(), task["id"], status)
    return ok


async def _write_prompt(task: dict | None, text: str, *, replace: bool) -> str:
    if not task:
        return "No task to edit."
    if not text:
        return "What should I add to the prompt?"
    current = task.get("composer_prompt") or ""
    new = text if replace else (current.rstrip() + "\n" + text).strip()
    db = await get_db()
    await db.execute("UPDATE tasks SET composer_prompt = ? WHERE id = ?", (new, task["id"]))
    await db.commit()
    return "Prompt replaced." if replace else "Prompt updated."


async def _status_text(task: dict | None) -> str:
    if not task:
        return "No active task."
    line = f"PR #{task['pr_number']} {task['repo']} — {task['severity']} / {task['status']}."
    if task.get("cursor_agent_id"):
        try:
            status = await cursor.get_run_status(task["cursor_agent_id"])
            line += "\n" + format_status_reply(
                task["cursor_agent_id"], status, task.get("accepted_at")
            )
        except Exception as exc:
            line += f" Agent status unavailable ({exc})."
    return line


async def _stop(task: dict | None) -> str:
    if not task or not task.get("cursor_agent_id"):
        return "No running agent to stop."
    agent_id = task["cursor_agent_id"]
    usage = None
    try:
        usage = (await cursor.get_run_status(agent_id)).get("token_usage")
    except Exception:
        logger.warning("cursor status before stop failed", exc_info=True)
    await cursor.cancel_agent(agent_id)
    try:
        await update_task_status(await get_db(), task["id"], "dismissed")
    except ValueError:
        pass
    line = f"Stopped `{agent_id}`."
    if usage:
        line += f" Tokens {usage}."
    return line


async def _agent_running(task: dict) -> bool:
    if not task.get("cursor_agent_id"):
        return False
    if task.get("status") not in ("accepted", "in_progress"):
        return False
    try:
        state = await cursor.get_run_status(task["cursor_agent_id"])
    except Exception:
        logger.warning("agent running check failed", exc_info=True)
        return True
    return state.get("status") == "running"


async def _launch(task: dict | None, params: dict, channel=None) -> str:
    if not task:
        return "No task to launch against."
    if await _agent_running(task):
        return "Agent is already running — use /status to check progress."
    prompt = params.get("prompt") or task.get("composer_prompt") or task.get("suggested_fix") or ""
    if params.get("branch"):
        result = await cursor.launch_agent(
            repo_full_name=task["repo"],
            prompt=prompt,
            model=params.get("model"),
            branch=params["branch"],
        )
    else:
        result = await cursor.launch_on_pull(
            task["repo"], int(task["pr_number"]), prompt, model=params.get("model")
        )
    db = await get_db()
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ?, fix_attempts = ? WHERE id = ?",
        (result["agent_id"], 1, task["id"]),
    )
    await db.commit()
    await accept_task(db, task["id"])
    if channel is not None:
        from app.discord.views import monitor_agent

        asyncio.create_task(monitor_agent(result["agent_id"], channel, task["id"]))
    return f"Launched `{result['agent_id']}` with {model_label(result['model'])}."


async def _resume(task: dict | None, message: str) -> str:
    if not task or not task.get("cursor_agent_id"):
        return "No agent to resume. I can launch one instead."
    result = await cursor.resume_agent(task["cursor_agent_id"], message or None)
    return f"Sent that to the agent. Status: {result.get('status')}."


async def _rediagnose(task: dict | None, params: dict) -> str:
    if not task:
        return "No task for that PR."
    owner, repo = _split_repo(task["repo"])
    from app.routes.webhook import _agent

    asyncio.create_task(
        _agent.run(str(uuid.uuid4()), owner, repo, int(task["pr_number"]), task["head_sha"])
    )
    return f"Re-running diagnosis on PR #{task['pr_number']}."


def format_prompt_history(raw, current: str = "") -> str:
    """Each stored attempt, then the prompt that is current now."""
    try:
        history = json.loads(raw or "[]")
    except json.JSONDecodeError:
        history = []
    if not isinstance(history, list):
        history = []
    lines = []
    for item in history:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"Attempt {item.get('attempt')}: {item.get('gate_result') or 'no gate result'}\n"
            f"{_clip(item.get('prompt') or '', 400)}"
        )
    if current:
        lines.append(f"Current prompt:\n{_clip(current, 400)}")
    return "\n\n".join(lines) or "No attempts stored."


def _attempts_text(task: dict | None) -> str:
    if not task:
        return "No task to show attempts for."
    return format_prompt_history(task.get("prompt_history"), task.get("composer_prompt") or "")


async def _trace(task: dict | None) -> str:
    if not task:
        return "No task to show a trace for."
    db = await get_db()
    cur = await db.execute(
        "SELECT phase, content FROM trace_steps WHERE task_id = ? ORDER BY id",
        (task["id"],),
    )
    lines = [f"[{row['phase']}] {row['content'][:160]}" for row in await cur.fetchall()]
    body = "\n".join(lines[:20]) or "No trace steps recorded."
    return f"```\n{_clip(body)}\n```"


async def _tasks(params: dict) -> str:
    status = params.get("status") or "all"
    limit = int(params.get("limit") or 5)
    db = await get_db()
    if status == "all":
        cur = await db.execute(
            "SELECT pr_number, repo, severity, status, diagnosis_json FROM tasks "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    else:
        cur = await db.execute(
            "SELECT pr_number, repo, severity, status, diagnosis_json FROM tasks "
            "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    lines = []
    for row in await cur.fetchall():
        row = dict(row)
        missing = (_loads(row.get("diagnosis_json")).get("intent_alignment") or {}).get("missing") or []
        extra = f" — {len(missing)} missing reqs" if missing else ""
        lines.append(
            f"**PR #{row['pr_number']}** — {row['repo']} ({row['severity']}){extra} — {row['status']}"
        )
    return "\n".join(lines) or "No tasks."
