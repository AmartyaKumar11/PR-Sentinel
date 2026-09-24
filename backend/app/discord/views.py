"""Approve / review buttons. Task id is in the button custom_id."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

import discord

from app.config import settings
from app.database import get_db
from app.services.cursor_client import cursor, model_label
from app.services.github_client import GitHubClient
from app.services.task_manager import delete_pending, get_pending, get_task, update_task_status

logger = logging.getLogger(__name__)

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


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


async def _run_button(interaction: discord.Interaction, view: discord.ui.View, work) -> None:
    """Defer, run the click, and always answer. A thrown launch must not stick on thinking."""
    if not await owner_only(interaction):
        return
    await interaction.response.defer(thinking=True)
    try:
        await work()
    except Exception as exc:
        logger.exception("discord button failed")
        await interaction.followup.send(f"⚠️ Failed: {exc}", ephemeral=True)
    for child in view.children:
        child.disabled = True
    if interaction.message:
        try:
            await interaction.message.edit(view=view)
        except Exception:
            logger.exception("could not disable discord buttons")


class ApprovalView(discord.ui.View):
    def __init__(self, task_id: str, repo: str, pr_number: int):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.repo = repo
        self.pr_number = pr_number
        _tag(self, task_id)

    @discord.ui.button(label="Approve Fix", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def work():
            db = await get_db()
            task = await get_task(db, self.task_id)
            if not task:
                await interaction.followup.send("Task not found.", ephemeral=True)
                return
            await update_task_status(db, self.task_id, "accepted")
            result = await cursor.launch_agent(
                repo_full_name=self.repo,
                prompt=task.get("composer_prompt") or "",
                branch=f"pr-sentinel/fix-{self.pr_number}",
            )
            await db.execute(
                "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
                (result["agent_id"], self.task_id),
            )
            await db.commit()
            await interaction.followup.send(
                f"🚀 Cursor Cloud Agent launched!\n"
                f"**Agent ID:** `{result['agent_id']}`\n"
                f"**Model:** {model_label(result['model'])}\n"
                f"**Repo:** {result['repo']}\n\n"
                f"Use `/status` to check progress, `/stop` to cancel."
            )
            asyncio.create_task(monitor_agent(result["agent_id"], interaction.channel, self.task_id))

        await _run_button(interaction, self, work)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def work():
            db = await get_db()
            await update_task_status(db, self.task_id, "dismissed")
            await interaction.followup.send("Task dismissed.", ephemeral=True)

        await _run_button(interaction, self, work)

    @discord.ui.button(label="View Details", style=discord.ButtonStyle.blurple, emoji="📋")
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def work():
            db = await get_db()
            task = await get_task(db, self.task_id)
            raw = (task or {}).get("diagnosis_json") or "{}"
            diag = json_loads(raw)
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

        await _run_button(interaction, self, work)

async def monitor_agent(agent_id: str, channel, task_id: str) -> None:
    """Poll until the cloud agent finishes. A few failed checks do not end the watch."""
    from app.discord.bot import bot

    failures = 0
    max_failures = 10
    while failures < max_failures:
        await asyncio.sleep(15)
        try:
            status = await cursor.get_run_status(agent_id)
            failures = 0
        except Exception as exc:
            failures += 1
            logger.exception("cursor status failed agent=%s (%s/%s)", agent_id, failures, max_failures)
            if failures >= max_failures:
                await channel.send(
                    f"⚠️ Lost contact with agent `{agent_id}` "
                    f"after {max_failures} failed checks. "
                    f"Last error: {exc}\n"
                    f"Check cursor.com/agents for its status."
                )
                return
            continue
        state = status.get("status")
        if state == "completed":
            if not status.get("pr_url"):
                await bot.send_agent_complete(task_id, status)
                return
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
            await bot.send_gate_result(task_id, status, gate)
            return
        if state in ("failed", "cancelled", "expired"):
            await channel.send(
                f"⚠️ Agent `{agent_id}` {state}."
                f"\n{status.get('result_text') or 'No details.'}"
            )
            return


class AnalyzeView(discord.ui.View):
    def __init__(self, pending_id: str):
        super().__init__(timeout=None)
        self.pending_id = pending_id
        _tag(self, pending_id)

    @discord.ui.button(label="Analyze with PR Sentinel", style=discord.ButtonStyle.green, emoji="🔍")
    async def analyze(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def work():
            db = await get_db()
            pending = await get_pending(db, self.pending_id)
            if not pending:
                await interaction.followup.send("This PR is no longer waiting for analysis.", ephemeral=True)
                return
            await delete_pending(db, self.pending_id)
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

        await _run_button(interaction, self, work)


class ReviewView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        _tag(self, task_id)

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _merge_pr(interaction, self.pr_url))

    @discord.ui.button(label="Reject Fix", style=discord.ButtonStyle.red, emoji="🚫")
    async def reject_fix(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def work():
            parsed = parse_pr_url(self.pr_url or "")
            if parsed:
                await GitHubClient().close_pr(*parsed)
            await interaction.followup.send(
                "Fix PR closed. Task re-dispatched for manual fix.", ephemeral=True
            )

        await _run_button(interaction, self, work)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _relaunch(interaction, self.task_id))


class FailedGateView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        self.confirmed = False
        _tag(self, task_id)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _relaunch(interaction, self.task_id))

    @discord.ui.button(label="Override & Merge", style=discord.ButtonStyle.green, emoji="⚠️")
    async def override(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await owner_only(interaction):
            return
        if not self.confirmed:
            self.confirmed = True
            parsed = parse_pr_url(self.pr_url or "")
            number = parsed[2] if parsed else "?"
            await interaction.response.send_message(
                f"Are you sure? This will merge PR #{number}. Click Override & Merge again to confirm.",
                ephemeral=True,
            )
            return
        await _run_button(interaction, self, lambda: _merge_pr(interaction, self.pr_url))

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.red, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _dismiss(interaction, self.task_id))


class PartialGateView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        _tag(self, task_id)

    @discord.ui.button(label="Merge Anyway", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _merge_pr(interaction, self.pr_url))

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _relaunch(interaction, self.task_id))

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.red, emoji="🗑️")
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _run_button(interaction, self, lambda: _dismiss(interaction, self.task_id))


async def _merge_pr(interaction: discord.Interaction, pr_url: str | None) -> None:
    parsed = parse_pr_url(pr_url or "")
    if not parsed:
        await interaction.followup.send("No PR URL available.", ephemeral=True)
        return
    owner, repo, pr_num = parsed
    result = await GitHubClient().merge_pr(owner, repo, pr_num)
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
    result = await cursor.launch_agent(
        repo_full_name=task["repo"],
        prompt=task.get("composer_prompt") or "",
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


