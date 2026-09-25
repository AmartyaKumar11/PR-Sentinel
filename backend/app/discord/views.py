"""Approve / review buttons. Task id is in the button custom_id."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

import discord

from app.config import settings
from app.database import get_db
from app.services.cursor_client import cursor, format_progress, model_label
from app.services.github_client import GitHubClient
from app.services.task_manager import (
    accept_task,
    delete_pending,
    get_pending,
    get_task,
    update_task_status,
)

logger = logging.getLogger(__name__)

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")
MAX_ATTEMPTS = 3


def append_prompt_history(raw, attempt: int, prompt: str, summary: str) -> str:
    """Keep the prompt we are about to replace."""
    import json

    try:
        existing = json.loads(raw or "[]")
    except json.JSONDecodeError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append({
        "attempt": attempt,
        "prompt": prompt or "",
        "gate_result": summary or "",
    })
    return json.dumps(existing)


def refining_message(attempt: int) -> str | None:
    """Discord line for the next try. None means the user can see the failure."""
    if attempt >= MAX_ATTEMPTS:
        return None
    return f"🔄 Refining fix (attempt {attempt + 1}/{MAX_ATTEMPTS})..."


async def owner_only(interaction: discord.Interaction) -> bool:
    if interaction.user.id != int(settings.DISCORD_OWNER_ID):
        await interaction.response.send_message(
            "Only the repo owner can do this.", ephemeral=True
        )
        return False
    return True


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    match = _PR_URL.search(url or "")
    if not match:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def _tag(view: discord.ui.View, task_id: str) -> None:
    for child in view.children:
        label = getattr(child, "label", "") or ""
        slug = label.split()[0].lower()
        child.custom_id = f"prs:{slug}:{task_id}"[:100]


_claimed: set[int] = set()
_override_ready: set[str] = set()
_busy: set[str] = set()
_GUARDED = {"Approve Fix", "Analyze with PR Sentinel", "Merge", "Merge Anyway", "Re-run Agent"}


def component_parts(custom_id: str) -> tuple[str, str]:
    parts = (custom_id or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "prs" or not parts[1] or not parts[2]:
        return "", ""
    return parts[1], parts[2]


def claim_interaction(interaction: discord.Interaction) -> bool:
    """One click must launch once. The view callback and the fallback listener both call in."""
    if interaction.id in _claimed or interaction.response.is_done():
        return False
    _claimed.add(interaction.id)
    return True


def _component_label(interaction: discord.Interaction) -> str:
    custom_id = (interaction.data or {}).get("custom_id")
    message = interaction.message
    if not message or not custom_id:
        return ""
    for row in message.components:
        for child in getattr(row, "children", ()) or ():
            if getattr(child, "custom_id", None) == custom_id:
                return child.label or ""
    return ""


def _pr_url_from_message(interaction: discord.Interaction) -> str | None:
    message = interaction.message
    if not message:
        return None
    for embed in message.embeds:
        for field in embed.fields:
            if field.name == "Pull Request" and field.value:
                return field.value
    return None


async def _disable_buttons(interaction: discord.Interaction) -> None:
    message = interaction.message
    if not message or not message.components:
        return
    try:
        view = discord.ui.View.from_message(message, timeout=None)
        for child in view.children:
            child.disabled = True
        await message.edit(view=view)
    except Exception:
        logger.exception("could not disable discord buttons")


def _take(key: str) -> bool:
    if key in _busy:
        return False
    _busy.add(key)
    return True


def _release(key: str) -> None:
    _busy.discard(key)


async def _reserve(label: str, entity_id: str) -> str | None:
    """None means this click may proceed. The action is reserved."""
    if label == "Approve Fix":
        task = await get_task(await get_db(), entity_id)
        if task and task.get("status") in ("accepted", "in_progress"):
            return "Already approved — agent is running."
        if task and task.get("cursor_agent_id"):
            return "Agent already launched."
        if not _take(f"approve:{entity_id}"):
            return "Already approved — agent is running."
        return None
    if label == "Analyze with PR Sentinel":
        if not await get_pending(await get_db(), entity_id):
            return "Already analyzed."
        if not _take(f"analyze:{entity_id}"):
            return "Already analyzed."
        return None
    if label in ("Merge", "Merge Anyway"):
        if not _take(f"merge:{entity_id}"):
            return "Already merging this pull request."
        return None
    if label == "Re-run Agent":
        task = await get_task(await get_db(), entity_id)
        if task and task.get("cursor_agent_id"):
            try:
                state = await cursor.get_run_status(task["cursor_agent_id"])
                if state.get("status") == "running":
                    return "Agent already launched."
            except Exception:
                logger.warning("rerun status check failed", exc_info=True)
        if not _take(f"rerun:{entity_id}"):
            return "Agent already launched."
        return None
    return None


def _busy_key(label: str, entity_id: str) -> str:
    if label == "Approve Fix":
        return f"approve:{entity_id}"
    if label == "Analyze with PR Sentinel":
        return f"analyze:{entity_id}"
    if label in ("Merge", "Merge Anyway"):
        return f"merge:{entity_id}"
    return f"rerun:{entity_id}"


async def _ack_disable(interaction: discord.Interaction) -> None:
    """Disable the buttons as the interaction acknowledgement."""
    message = interaction.message
    if message and message.components:
        try:
            view = discord.ui.View.from_message(message, timeout=None)
            for child in view.children:
                child.disabled = True
            await interaction.response.edit_message(view=view)
            return
        except Exception:
            logger.warning("could not disable buttons on ack", exc_info=True)
    if not interaction.response.is_done():
        await interaction.response.defer()


async def _run_button(interaction: discord.Interaction, work, label: str = "", entity_id: str = "") -> None:
    """Ack the click, then do the work. A second click does not start a second action."""
    if not await owner_only(interaction):
        return
    guarded = label in _GUARDED
    if guarded:
        blocked = await _reserve(label, entity_id)
        if blocked:
            await interaction.response.send_message(blocked, ephemeral=True)
            return
        await _ack_disable(interaction)
    else:
        await interaction.response.defer()
    try:
        await work()
    except Exception as exc:
        logger.exception("discord button failed")
        if guarded:
            _release(_busy_key(label, entity_id))
        await interaction.followup.send(f"⚠️ Failed: {exc}", ephemeral=True)
        return
    if not guarded:
        await _disable_buttons(interaction)


class ApprovalView(discord.ui.View):
    def __init__(self, task_id: str, repo: str, pr_number: int):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.repo = repo
        self.pr_number = pr_number
        _tag(self, task_id)

    @discord.ui.button(label="Approve Fix", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="View Details", style=discord.ButtonStyle.blurple, emoji="📋")
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

async def _edit_or_send(msg, channel, content: str):
    if msg is not None:
        try:
            await msg.edit(content=content)
            return
        except Exception:
            logger.warning("progress edit failed", exc_info=True)
    await channel.send(content)


async def _read_head(repo: str, pr_number: int) -> dict:
    owner, name = repo.split("/", 1)
    github = GitHubClient()
    try:
        info = await github.get_pr_info(owner, name, int(pr_number))
    finally:
        await github.close()
    info["url"] = f"https://github.com/{repo}/pull/{int(pr_number)}"
    return info


def _no_push_gate() -> dict:
    return {
        "passed": False,
        "verdict": "failed",
        "ci_status": "no_ci",
        "summary": "Agent finished without pushing a commit to the branch.",
        "requirement_alignment": {},
        "diff_sanity": {},
    }


async def monitor_agent(agent_id: str, channel, task_id: str) -> None:
    """Poll until the cloud agent finishes. One Discord message, edited in place."""
    from app.discord.bot import bot

    pr_number = "?"
    attempt = 1
    current_agent_id = agent_id
    before_sha = ""
    branch = ""
    pr_url = ""
    try:
        task = await get_task(await get_db(), task_id)
        if task and task.get("pr_number") is not None:
            pr_number = task["pr_number"]
            pr_url = f"https://github.com/{task['repo']}/pull/{pr_number}"
            try:
                info = await _read_head(task["repo"], int(pr_number))
                before_sha = info.get("head_sha") or task.get("head_sha") or ""
                branch = info.get("branch") or ""
                pr_url = info["url"]
            except Exception:
                logger.warning("head sha lookup failed", exc_info=True)
                before_sha = task.get("head_sha") or ""
        if task and task.get("fix_attempts"):
            attempt = int(task["fix_attempts"])
    except Exception:
        logger.warning("progress pr lookup failed", exc_info=True)
    msg = None
    try:
        msg = await channel.send(f"🔧 **Agent working on PR #{pr_number}**\nStatus: starting")
    except Exception:
        logger.exception("progress message failed")

    failures = 0
    max_failures = 10
    while failures < max_failures:
        await asyncio.sleep(15)
        try:
            status = await cursor.get_run_status(current_agent_id)
            failures = 0
        except Exception as exc:
            failures += 1
            logger.exception("cursor status failed agent=%s (%s/%s)", current_agent_id, failures, max_failures)
            if failures >= max_failures:
                await _edit_or_send(
                    msg,
                    channel,
                    f"⚠️ Lost contact with agent `{current_agent_id}` "
                    f"after {max_failures} failed checks. "
                    f"Last error: {exc}\n"
                    f"Check cursor.com/agents for its status.",
                )
                return
            continue
        state = status.get("status")
        if state == "completed":
            task = await get_task(await get_db(), task_id)
            if not task or task.get("pr_number") is None:
                await bot.send_agent_complete(task_id, status, message=msg)
                return
            try:
                info = await _read_head(task["repo"], int(task["pr_number"]))
            except Exception:
                logger.exception("head sha after agent failed task=%s", task_id)
                info = {"head_sha": before_sha, "branch": branch, "url": pr_url}
            status["pr_url"] = info.get("url") or pr_url
            status["branch"] = info.get("branch") or branch
            status["pushed_to_pr"] = task["pr_number"]
            if before_sha and info.get("head_sha") == before_sha:
                gate = _no_push_gate()
            else:
                try:
                    gate = await _store_gate(task_id, status)
                except Exception as exc:
                    logger.exception("quality gate failed task=%s", task_id)
                    gate = {
                        "passed": False,
                        "verdict": "failed",
                        "ci_status": "failed",
                        "summary": f"Quality gate failed: {exc}",
                    }
            if gate.get("passed"):
                await bot.send_gate_result(task_id, status, gate, message=msg)
                return
            notice = refining_message(attempt)
            if notice:
                launched = await _launch_retry(channel, task_id, status, gate, attempt, notice)
                if launched:
                    attempt += 1
                    current_agent_id = launched
                    failures = 0
                    if info.get("head_sha"):
                        before_sha = info["head_sha"]
                    continue
            await _send_exhausted(channel, task_id, status, gate)
            return
        if state in ("failed", "cancelled", "expired"):
            await _edit_or_send(
                msg,
                channel,
                f"⚠️ Agent `{current_agent_id}` {state}."
                f"\n{status.get('result_text') or status.get('last_activity') or 'No details.'}",
            )
            return
        if msg is not None:
            shown = dict(status)
            if pr_url:
                shown["pr_url"] = shown.get("pr_url") or pr_url
            if branch and not shown.get("branch"):
                shown["branch"] = branch
            try:
                await msg.edit(content=format_progress(pr_number, shown))
            except Exception:
                logger.warning("progress edit failed", exc_info=True)


class AnalyzeView(discord.ui.View):
    def __init__(self, pending_id: str):
        super().__init__(timeout=None)
        self.pending_id = pending_id
        _tag(self, pending_id)

    @discord.ui.button(label="Analyze with PR Sentinel", style=discord.ButtonStyle.green, emoji="🔍")
    async def analyze(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)


class ReviewView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        _tag(self, task_id)

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Reject Fix", style=discord.ButtonStyle.red, emoji="🚫")
    async def reject_fix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)


class OverrideView(discord.ui.View):
    """Shown only after every automatic retry has failed."""

    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        self.confirmed = False
        _tag(self, task_id)

    @discord.ui.button(label="Override & Merge", style=discord.ButtonStyle.green, emoji="⚠️")
    async def override(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.red, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)


class FailedGateView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        self.confirmed = False
        _tag(self, task_id)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Override & Merge", style=discord.ButtonStyle.green, emoji="⚠️")
    async def override(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.red, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)


class PartialGateView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        _tag(self, task_id)

    @discord.ui.button(label="Merge Anyway", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.red, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_component(interaction)


async def handle_component(interaction: discord.Interaction) -> None:
    """Answer a button even when the in-memory view was lost."""
    if interaction.type != discord.InteractionType.component:
        return
    slug, entity_id = component_parts((interaction.data or {}).get("custom_id") or "")
    if not slug:
        return
    if not claim_interaction(interaction):
        return
    label = _component_label(interaction)
    logger.info("discord component %s %s", label or slug, entity_id)
    if label == "Override & Merge":
        await _override_click(interaction, entity_id)
        return

    async def work():
        await _dispatch_label(label, entity_id, interaction)

    await _run_button(interaction, work, label, entity_id)


async def _dispatch_label(label: str, entity_id: str, interaction: discord.Interaction) -> None:
    if label == "Approve Fix":
        await _approve_fix(interaction, entity_id)
    elif label == "Reject":
        await _dismiss(interaction, entity_id)
    elif label == "View Details":
        await _show_details(interaction, entity_id)
    elif label == "Analyze with PR Sentinel":
        await _analyze_pending(interaction, entity_id)
    elif label in ("Merge", "Merge Anyway"):
        await _merge_pr(interaction, _pr_url_from_message(interaction))
    elif label == "Reject Fix":
        parsed = parse_pr_url(_pr_url_from_message(interaction) or "")
        if parsed:
            await GitHubClient().close_pr(*parsed)
        await interaction.followup.send(
            "Fix PR closed. Task re-dispatched for manual fix.", ephemeral=True
        )
    elif label == "Re-run Agent":
        await _relaunch(interaction, entity_id)
    elif label == "Dismiss":
        await _dismiss(interaction, entity_id)
    else:
        await interaction.followup.send(f"Unknown button: {label or 'unlabeled'}", ephemeral=True)


async def _override_click(interaction: discord.Interaction, entity_id: str) -> None:
    if not await owner_only(interaction):
        return
    pr_url = _pr_url_from_message(interaction)
    if entity_id not in _override_ready:
        _override_ready.add(entity_id)
        parsed = parse_pr_url(pr_url or "")
        number = parsed[2] if parsed else "?"
        await interaction.response.send_message(
            f"Are you sure? This will merge PR #{number}. Click Override & Merge again to confirm.",
            ephemeral=True,
        )
        return
    await _run_button(interaction, lambda: _merge_pr(interaction, pr_url), "Merge", entity_id)


async def _approve_fix(interaction: discord.Interaction, task_id: str) -> None:
    db = await get_db()
    task = await get_task(db, task_id)
    if not task:
        await interaction.followup.send("Task not found.", ephemeral=True)
        return
    await accept_task(db, task_id)
    result = await cursor.launch_on_pull(
        task["repo"], int(task["pr_number"]), task.get("composer_prompt") or ""
    )
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ?, fix_attempts = ? WHERE id = ?",
        (result["agent_id"], 1, task_id),
    )
    await db.commit()
    await interaction.followup.send(
        f"🚀 Cursor Cloud Agent launched!\n"
        f"**Agent ID:** `{result['agent_id']}`\n"
        f"**Model:** {model_label(result['model'])}\n"
        f"**Branch:** `{result['branch']}`\n"
        f"**PR:** {result['pr_url']}\n\n"
        f"Use `/status` to check progress, `/stop` to cancel."
    )
    asyncio.create_task(monitor_agent(result["agent_id"], interaction.channel, task_id))


async def _show_details(interaction: discord.Interaction, task_id: str) -> None:
    db = await get_db()
    task = await get_task(db, task_id)
    diag = json_loads((task or {}).get("diagnosis_json"))
    missing = (diag.get("intent_alignment") or {}).get("missing") or []
    creep = (diag.get("intent_alignment") or {}).get("scope_creep") or []
    blast = diag.get("blast_radius") or {}
    lines = ["**Missing requirements:**"]
    lines += [f"  ⚠️ {m}" for m in missing] or ["  none"]
    lines.append("\n**Scope creep:**")
    lines += [f"  🔀 {s}" for s in creep] or ["  none"]
    lines.append(f"\n**Blast radius:** risk {blast.get('risk_score', 0):.2f}")
    lines.append(f"**Path:** `{blast.get('highest_risk_path') or 'N/A'}`")
    untested = ", ".join(blast.get("untested_impacted") or []) or "all covered"
    lines.append(f"**Untested:** {untested}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


async def _analyze_pending(interaction: discord.Interaction, pending_id: str) -> None:
    db = await get_db()
    pending = await get_pending(db, pending_id)
    if not pending:
        await interaction.followup.send("This PR is no longer waiting for analysis.", ephemeral=True)
        return
    await delete_pending(db, pending_id)
    await interaction.followup.send(f"🔍 Analyzing PR #{pending['pr_number']}...")
    owner, repo = pending["repo"].split("/", 1)
    from app.routes.webhook import _agent

    asyncio.create_task(
        _agent.run(
            str(uuid.uuid4()),
            owner,
            repo,
            int(pending["pr_number"]),
            pending["head_sha"],
            mode="full",
        )
    )


async def _merge_pr(interaction: discord.Interaction, pr_url: str | None) -> None:
    parsed = parse_pr_url(pr_url or "")
    if not parsed:
        await interaction.followup.send("No PR URL available.", ephemeral=True)
        return
    owner, repo, pr_num = parsed
    result = await GitHubClient().merge_pr_safe(owner, repo, pr_num)
    if result.get("merged"):
        await interaction.followup.send(f"✅ PR #{pr_num} merged!")
    else:
        await interaction.followup.send(f"❌ Merge failed: {result.get('message', 'unknown error')}")


async def _relaunch(interaction: discord.Interaction, task_id: str) -> None:
    db = await get_db()
    task = await get_task(db, task_id)
    if not task:
        await interaction.followup.send("Task not found.", ephemeral=True)
        return
    result = await cursor.launch_on_pull(
        task["repo"], int(task["pr_number"]), task.get("composer_prompt") or ""
    )
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
        (result["agent_id"], task_id),
    )
    await db.commit()
    await interaction.followup.send(
        f"🔄 Re-launched agent `{result['agent_id']}` with model {model_label(result['model'])}"
    )
    asyncio.create_task(monitor_agent(result["agent_id"], interaction.channel, task_id))


async def _dismiss(interaction: discord.Interaction, task_id: str) -> None:
    try:
        await update_task_status(await get_db(), task_id, "dismissed")
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send("Task dismissed.", ephemeral=True)


async def _launch_retry(channel, task_id: str, status: dict, gate: dict, attempt: int, notice: str) -> str | None:
    """Start the next agent. Returns the new agent id, or None if the launch itself failed."""
    from app.services.llm_client import LLMClient
    from app.services.retry_diagnostics import build_fallback_retry_prompt, build_retry_prompt

    db = await get_db()
    task = await get_task(db, task_id)
    if not task:
        return None
    pr_url = f"https://github.com/{task['repo']}/pull/{task['pr_number']}"
    try:
        retry_prompt = await build_retry_prompt(task_id, gate, pr_url, LLMClient())
    except Exception as exc:
        logger.exception("retry diagnosis failed task=%s", task_id)
        retry_prompt = build_fallback_retry_prompt(
            gate,
            task.get("composer_prompt") or "",
            json_loads(task.get("diagnosis_json")),
        )
        await channel.send(
            f"⚠️ Detailed analysis failed ({str(exc)[:100]}), retrying with simplified feedback..."
        )
    await channel.send(notice)
    try:
        result = await cursor.launch_on_pull(task["repo"], int(task["pr_number"]), retry_prompt)
    except Exception:
        logger.exception("retry launch failed task=%s", task_id)
        return None
    next_attempt = attempt + 1
    history = append_prompt_history(
        task.get("prompt_history"),
        attempt,
        task.get("composer_prompt") or "",
        gate.get("summary") or "",
    )
    await db.execute(
        "UPDATE tasks SET cursor_agent_id = ?, fix_attempts = ?, prompt_history = ?, composer_prompt = ? WHERE id = ?",
        (result["agent_id"], next_attempt, history, retry_prompt, task_id),
    )
    await db.commit()
    return result["agent_id"]


async def _send_exhausted(channel, task_id: str, status: dict, gate: dict) -> None:
    summary = gate.get("summary") or "unknown"
    await channel.send(
        f"⚠️ Fix didn't pass after {MAX_ATTEMPTS} attempts.\n**Last issue:** {summary}"
    )
    await channel.send(
        "You can override and merge, or dismiss.",
        view=OverrideView(task_id=task_id, pr_url=status.get("pr_url")),
    )


async def _store_gate(task_id: str, status: dict) -> dict:
    import json

    from app.services.quality_gate import validate

    parsed = parse_pr_url(status.get("pr_url") or "")
    if not parsed:
        raise RuntimeError("Fix PR URL is missing")
    db = await get_db()
    task = await get_task(db, task_id)
    diag = json_loads((task or {}).get("diagnosis_json"))
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
            diag,
            github=github,
        )
    finally:
        await github.close()
    await db.execute(
        "UPDATE tasks SET quality_gate_json = ? WHERE id = ?",
        (json.dumps(gate), task_id),
    )
    await db.commit()
    return gate


def json_loads(raw) -> dict:
    import json

    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


