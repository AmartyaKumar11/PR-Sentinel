"""Approve / review buttons. Task id is in the button custom_id."""

from __future__ import annotations

import asyncio
import logging
import re

import discord

from app.database import get_db
from app.services.cursor_client import CursorClient
from app.services.github_client import GitHubClient
from app.services.task_manager import get_task, update_task_status

logger = logging.getLogger(__name__)
cursor = CursorClient()

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


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


class ApprovalView(discord.ui.View):
    def __init__(self, task_id: str, repo: str, pr_number: int):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.repo = repo
        self.pr_number = pr_number
        _tag(self, task_id)

    @discord.ui.button(label="Approve Fix", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
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
            f"**Model:** {result['model']}\n"
            f"**Repo:** {result['repo']}\n\n"
            f"Use `/status` to check progress, `/stop` to cancel."
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        asyncio.create_task(self._monitor_agent(result["agent_id"], interaction.channel))

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = await get_db()
        await update_task_status(db, self.task_id, "dismissed")
        await interaction.response.send_message("Task dismissed.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="View Details", style=discord.ButtonStyle.blurple, emoji="📋")
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = await get_db()
        task = await get_task(db, self.task_id)
        raw = task.get("diagnosis_json") or "{}"
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
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def _monitor_agent(self, agent_id: str, channel):
        from app.discord.bot import bot

        while True:
            await asyncio.sleep(10)
            try:
                status = await cursor.get_run_status(agent_id)
            except Exception:
                logger.exception("cursor status failed agent=%s", agent_id)
                return
            if status["status"] not in ("completed", "failed", "cancelled", "expired"):
                continue
            status["quality_warning"] = await _quality_warning(self.task_id, status)
            await bot.send_agent_complete(self.task_id, status)
            return


class ReviewView(discord.ui.View):
    def __init__(self, task_id: str, pr_url: str | None = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url
        _tag(self, task_id)

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        parsed = parse_pr_url(self.pr_url or "")
        if not parsed:
            await interaction.followup.send("No PR URL available.", ephemeral=True)
            return
        owner, repo, pr_num = parsed
        result = await GitHubClient().merge_pr(owner, repo, pr_num)
        if result.get("merged"):
            await interaction.followup.send(f"✅ PR #{pr_num} merged!")
        else:
            await interaction.followup.send(f"❌ Merge failed: {result.get('message', 'unknown error')}")
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Reject Fix", style=discord.ButtonStyle.red, emoji="🚫")
    async def reject_fix(self, interaction: discord.Interaction, button: discord.ui.Button):
        parsed = parse_pr_url(self.pr_url or "")
        if parsed:
            await GitHubClient().close_pr(*parsed)
        await interaction.response.send_message(
            "Fix PR closed. Task re-dispatched for manual fix.", ephemeral=True
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Re-run Agent", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def rerun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        db = await get_db()
        task = await get_task(db, self.task_id)
        if not task:
            await interaction.followup.send("Task not found.", ephemeral=True)
            return
        result = await cursor.launch_agent(
            repo_full_name=task["repo"],
            prompt=task.get("composer_prompt") or "",
        )
        await db.execute(
            "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
            (result["agent_id"], self.task_id),
        )
        await db.commit()
        await interaction.followup.send(
            f"🔄 Re-launched agent `{result['agent_id']}` with model {result['model']}"
        )


def json_loads(raw) -> dict:
    import json

    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


async def _quality_warning(task_id: str, status: dict) -> str | None:
    parsed = parse_pr_url(status.get("pr_url") or "")
    if not parsed:
        return None
    try:
        from typesafe_sdk import Noul

        from app.services.jev_client import JevClient

        db = await get_db()
        task = await get_task(db, task_id)
        diag = json_loads((task or {}).get("diagnosis_json"))
        missing = (diag.get("intent_alignment") or {}).get("missing") or []
        diff = await GitHubClient().get_pr_diff(*parsed)
        answers = await JevClient().evaluate(
            state={"fix_diff": diff[:4000], "original_missing": missing},
            questions={
                "addresses_all_requirements": Noul(
                    instructions="The fix diff addresses all the originally missing requirements"
                ),
                "introduces_new_issues": Noul(
                    instructions="The fix introduces obvious new bugs or breaks existing functionality"
                ),
            },
        )
        if answers["addresses_all_requirements"].noul < 0.5 or answers["introduces_new_issues"].noul > 0.5:
            return "Jev is not confident this diff covers the missing requirements. Read it before merging."
    except Exception:
        logger.warning("jev quality check skipped", exc_info=True)
    return None
