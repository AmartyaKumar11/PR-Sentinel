"""Slash commands for the phone loop."""

import re

import discord
from discord import app_commands

from app.config import settings
from app.database import get_db
from app.services.cursor_client import cursor
from app.services.github_client import GitHubClient

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


async def _active_agent(db):
    row = await db.execute(
        "SELECT id, cursor_agent_id, repo, pr_number, severity FROM tasks "
        "WHERE cursor_agent_id IS NOT NULL AND status NOT IN ('resolved','error','dismissed') "
        "ORDER BY created_at DESC LIMIT 1"
    )
    task = await row.fetchone()
    return dict(task) if task else None


async def setup_commands(bot):
    @bot.tree.command(name="status", description="Check the current Cursor agent status")
    async def status(interaction: discord.Interaction):
        db = await get_db()
        task = await _active_agent(db)
        if not task:
            await interaction.response.send_message("No active agent.", ephemeral=True)
            return
        result = await cursor.get_run_status(task["cursor_agent_id"])
        await interaction.response.send_message(
            f"**Agent:** `{task['cursor_agent_id']}`\n"
            f"**Status:** {result['status']}\n"
            f"**Branch:** {result.get('branch') or 'N/A'}\n"
            f"**PR:** {result.get('pr_url') or 'not yet'}\n"
            f"**Tokens:** {result.get('token_usage') or 'N/A'}"
        )

    @bot.tree.command(name="stop", description="Cancel the running Cursor agent")
    async def stop(interaction: discord.Interaction):
        db = await get_db()
        task = await _active_agent(db)
        if not task:
            await interaction.response.send_message("No active agent to stop.", ephemeral=True)
            return
        await cursor.cancel_agent(task["cursor_agent_id"])
        await interaction.response.send_message("⏹️ Agent cancelled.")

    @bot.tree.command(name="resume", description="Resume or re-send instructions to the agent")
    @app_commands.describe(message="Optional follow-up instructions")
    async def resume(interaction: discord.Interaction, message: str = None):
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No agent to resume.", ephemeral=True)
            return
        result = await cursor.resume_agent(dict(task)["cursor_agent_id"], message)
        await interaction.response.send_message(f"▶️ Agent resumed. Status: {result['status']}")

    @bot.tree.command(name="model", description="Change the model for the next agent run")
    @app_commands.describe(name="Model id, for example composer-2.5")
    async def model(interaction: discord.Interaction, name: str):
        cursor.default_model = name
        await interaction.response.send_message(f"Model set to `{name}` for next run.")

    @bot.tree.command(name="models", description="List available Cursor models")
    async def models(interaction: discord.Interaction):
        available = await cursor.list_models()
        model_list = "\n".join(f"  • `{m}`" for m in available) or "  none"
        await interaction.response.send_message(f"**Available models:**\n{model_list}")

    @bot.tree.command(name="diff", description="Show the current fix diff")
    async def diff(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No active agent.", ephemeral=True)
            return
        result = await cursor.get_run_status(dict(task)["cursor_agent_id"])
        pr_url = result.get("pr_url")
        if not pr_url:
            await interaction.response.send_message("Agent hasn't created a PR yet.", ephemeral=True)
            return
        match = _PR_URL.search(pr_url)
        if not match:
            await interaction.response.send_message(f"PR: {pr_url}")
            return
        diff_text = await GitHubClient().get_pr_diff(match.group(1), match.group(2), int(match.group(3)))
        if len(diff_text) > 1900:
            diff_text = diff_text[:1900] + "\n... (truncated)"
        await interaction.response.send_message(f"```diff\n{diff_text}\n```")

    @bot.tree.command(name="merge", description="Merge the fix PR")
    async def merge(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.followup.send("No task with a PR to merge.")
            return
        result = await cursor.get_run_status(dict(task)["cursor_agent_id"])
        match = _PR_URL.search(result.get("pr_url") or "")
        if not match:
            await interaction.followup.send("No PR to merge yet.")
            return
        merge_result = await GitHubClient().merge_pr(match.group(1), match.group(2), int(match.group(3)))
        if merge_result.get("merged"):
            await interaction.followup.send("✅ Merged! The VERIFY phase will run automatically.")
        else:
            await interaction.followup.send(f"❌ Merge failed: {merge_result.get('message')}")

    @bot.tree.command(name="reject", description="Close the fix PR and dismiss the task")
    async def reject(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute(
            "SELECT id, cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No fix PR to reject.", ephemeral=True)
            return
        task = dict(task)
        result = await cursor.get_run_status(task["cursor_agent_id"])
        match = _PR_URL.search(result.get("pr_url") or "")
        if match:
            await GitHubClient().close_pr(match.group(1), match.group(2), int(match.group(3)))
        from app.services.task_manager import update_task_status

        await update_task_status(db, task["id"], "dismissed")
        await interaction.response.send_message("Fix PR closed and task dismissed.")

    @bot.tree.command(name="logs", description="Show the agent reasoning trace")
    async def logs(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute("SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1")
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No tasks found.", ephemeral=True)
            return
        trace = await db.execute(
            "SELECT phase, type, content FROM trace_steps WHERE task_id = ? ORDER BY id",
            (dict(task)["id"],),
        )
        steps = await trace.fetchall()
        log_text = ""
        for s in steps[:15]:
            s = dict(s)
            emoji = {"thought": "💭", "action": "🔧", "observation": "👁", "answer": "✅"}.get(s["type"], "•")
            log_text += f"{emoji} [{s['phase']}] {s['content'][:100]}\n"
        await interaction.response.send_message(f"```\n{log_text or 'No trace steps recorded.'}\n```")

    @bot.tree.command(name="config", description="Show current PR Sentinel configuration")
    async def config(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**Model:** `{cursor.default_model}`\n"
            f"**Backend:** `{settings.DEEPSEEK_BASE_URL}`\n"
            f"**LLM:** {settings.LLM_MODEL}\n"
            f"**Decision engine:** Jev {settings.JEV_MODEL}\n"
            f"**Dashboard:** {settings.FRONTEND_URL}"
        )
