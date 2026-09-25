"""Slash commands for the phone loop."""

import logging
import re

import discord
from discord import app_commands

from app.config import settings
from app.discord.conversation import format_prompt_history
from app.discord.views import merge_with_conflict_resolution, owner_only, parse_pr_url, pr_url_for_task
from app.database import get_db
from app.services.cursor_client import cursor, format_status_reply, model_label
from app.services.github_client import GitHubClient
from app.services.task_manager import update_task_status

logger = logging.getLogger(__name__)

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


async def _active_agent(db):
    row = await db.execute(
        "SELECT id, cursor_agent_id, repo, pr_number, severity, accepted_at FROM tasks "
        "WHERE cursor_agent_id IS NOT NULL AND status NOT IN ('resolved','error','dismissed') "
        "ORDER BY created_at DESC LIMIT 1"
    )
    task = await row.fetchone()
    return dict(task) if task else None


async def _guard(interaction: discord.Interaction, work) -> None:
    if not await owner_only(interaction):
        return
    await interaction.response.defer(thinking=True)
    try:
        await work()
    except Exception as exc:
        logger.exception("slash command %s failed", getattr(interaction.command, "name", ""))
        await interaction.followup.send(f"⚠️ Failed: {exc}", ephemeral=True)


async def setup_commands(bot):
    @bot.tree.command(name="status", description="Check the current Cursor agent status")
    async def status(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            task = await _active_agent(db)
            if not task:
                await interaction.followup.send("No active agent.", ephemeral=True)
                return
            result = await cursor.get_run_status(task["cursor_agent_id"])
            await interaction.followup.send(
                format_status_reply(task["cursor_agent_id"], result, task.get("accepted_at"))
            )

        await _guard(interaction, work)

    @bot.tree.command(name="stop", description="Cancel the running Cursor agent")
    async def stop(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            task = await _active_agent(db)
            if not task:
                await interaction.followup.send("No active agent to stop.", ephemeral=True)
                return
            usage = None
            try:
                usage = (await cursor.get_run_status(task["cursor_agent_id"])).get("token_usage")
            except Exception:
                logger.warning("status before stop failed", exc_info=True)
            await cursor.cancel_agent(task["cursor_agent_id"])
            try:
                await update_task_status(db, task["id"], "dismissed")
            except ValueError:
                pass
            line = f"⏹️ Stopped `{task['cursor_agent_id']}`."
            if usage:
                line += f" Tokens {usage}."
            await interaction.followup.send(line)

        await _guard(interaction, work)

    @bot.tree.command(name="resume", description="Resume or re-send instructions to the agent")
    @app_commands.describe(message="Optional follow-up instructions")
    async def resume(interaction: discord.Interaction, message: str = None):
        async def work():
            db = await get_db()
            row = await db.execute(
                "SELECT cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
            task = await row.fetchone()
            if not task:
                await interaction.followup.send("No agent to resume.", ephemeral=True)
                return
            result = await cursor.resume_agent(dict(task)["cursor_agent_id"], message)
            await interaction.followup.send(f"▶️ Agent resumed. Status: {result['status']}")

        await _guard(interaction, work)

    @bot.tree.command(name="model", description="Change the model for the next agent run")
    @app_commands.describe(name="Model id, for example composer-2.5")
    async def model(interaction: discord.Interaction, name: str):
        async def work():
            cursor.default_model = name
            await interaction.followup.send(f"Model set to `{model_label(name)}` for next run.")

        await _guard(interaction, work)

    @bot.tree.command(name="models", description="List available Cursor models")
    async def models(interaction: discord.Interaction):
        async def work():
            available = await cursor.list_models()
            model_list = "\n".join(f"  • `{m}`" for m in available) or "  none"
            await interaction.followup.send(f"**Available models:**\n{model_list}")

        await _guard(interaction, work)

    @bot.tree.command(name="diff", description="Show the current fix diff")
    async def diff(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            row = await db.execute(
                "SELECT cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
            task = await row.fetchone()
            if not task:
                await interaction.followup.send("No active agent.", ephemeral=True)
                return
            result = await cursor.get_run_status(dict(task)["cursor_agent_id"])
            pr_url = result.get("pr_url")
            if not pr_url:
                await interaction.followup.send("Agent hasn't created a PR yet.", ephemeral=True)
                return
            match = _PR_URL.search(pr_url)
            if not match:
                await interaction.followup.send(f"PR: {pr_url}")
                return
            diff_text = await GitHubClient().get_pr_diff(match.group(1), match.group(2), int(match.group(3)))
            if len(diff_text) > 1900:
                diff_text = diff_text[:1900] + "\n... (truncated)"
            await interaction.followup.send(f"```diff\n{diff_text}\n```")

        await _guard(interaction, work)

    @bot.tree.command(name="merge", description="Merge the fix PR")
    async def merge(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            row = await db.execute(
                "SELECT repo, pr_number FROM tasks "
                "WHERE pr_number IS NOT NULL AND status NOT IN ('resolved', 'dismissed', 'error') "
                "ORDER BY created_at DESC LIMIT 1"
            )
            task = await row.fetchone()
            parsed = parse_pr_url(pr_url_for_task(dict(task) if task else None) or "")
            if not parsed:
                await interaction.followup.send("No PR to merge yet.")
                return
            merge_result = await merge_with_conflict_resolution(
                parsed[0], parsed[1], parsed[2], interaction.channel
            )
            if merge_result.get("announced"):
                return
            if merge_result.get("merged"):
                await interaction.followup.send("✅ Merged! The VERIFY phase will run automatically.")
            else:
                await interaction.followup.send(f"❌ Merge failed: {merge_result.get('message')}")

        await _guard(interaction, work)

    @bot.tree.command(name="reject", description="Close the fix PR and dismiss the task")
    async def reject(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            row = await db.execute(
                "SELECT id, cursor_agent_id FROM tasks WHERE cursor_agent_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
            task = await row.fetchone()
            if not task:
                await interaction.followup.send("No fix PR to reject.", ephemeral=True)
                return
            task = dict(task)
            result = await cursor.get_run_status(task["cursor_agent_id"])
            match = _PR_URL.search(result.get("pr_url") or "")
            if match:
                await GitHubClient().close_pr(match.group(1), match.group(2), int(match.group(3)))
            await update_task_status(db, task["id"], "dismissed")
            await interaction.followup.send("Fix PR closed and task dismissed.")

        await _guard(interaction, work)

    @bot.tree.command(name="logs", description="Show the agent reasoning trace")
    async def logs(interaction: discord.Interaction):
        async def work():
            db = await get_db()
            row = await db.execute(
                "SELECT id, prompt_history, composer_prompt FROM tasks ORDER BY created_at DESC LIMIT 1"
            )
            task = await row.fetchone()
            if not task:
                await interaction.followup.send("No tasks found.", ephemeral=True)
                return
            task = dict(task)
            trace = await db.execute(
                "SELECT phase, type, content FROM trace_steps WHERE task_id = ? ORDER BY id",
                (task["id"],),
            )
            steps = await trace.fetchall()
            log_text = ""
            for s in steps[:15]:
                s = dict(s)
                emoji = {"thought": "💭", "action": "🔧", "observation": "👁", "answer": "✅"}.get(s["type"], "•")
                log_text += f"{emoji} [{s['phase']}] {s['content'][:100]}\n"
            attempts = format_prompt_history(task.get("prompt_history"), task.get("composer_prompt") or "")
            await interaction.followup.send(
                f"```\n{log_text or 'No trace steps recorded.'}\n```\n{attempts[:1800]}"
            )

        await _guard(interaction, work)

    @bot.tree.command(name="config", description="Show current PR Sentinel configuration")
    async def config(interaction: discord.Interaction):
        async def work():
            await interaction.followup.send(
                f"**Model:** `{model_label(cursor.default_model)}`\n"
                f"**Backend:** `{settings.DEEPSEEK_BASE_URL}`\n"
                f"**LLM:** {settings.LLM_MODEL}\n"
                f"**Decision engine:** Jev {settings.JEV_MODEL}\n"
                f"**Dashboard:** {settings.FRONTEND_URL}"
            )

        await _guard(interaction, work)
