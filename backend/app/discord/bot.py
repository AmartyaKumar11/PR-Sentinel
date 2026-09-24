"""Discord bot started from the FastAPI lifespan when a token is set."""

import discord
from discord.ext import commands

from app.config import settings
from app.discord.commands import setup_commands
from app.discord.embeds import (
    build_agent_result_embed,
    build_needs_review_embed,
    build_task_embed,
    build_validation_failed_embed,
    build_verification_embed,
)
from app.discord.views import ApprovalView, FailedGateView, PartialGateView, ReviewView


class SentinelBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.channel = None
        self._pending_action = None

    async def setup_hook(self):
        await setup_commands(self)
        guild_id = settings.DISCORD_GUILD_ID
        if guild_id.isdigit():
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        if settings.DISCORD_CHANNEL_ID.isdigit():
            self.channel = self.get_channel(int(settings.DISCORD_CHANNEL_ID))
        name = self.channel.name if self.channel else "no-channel"
        print(f"PR Sentinel bot online in #{name}")

    async def send_task_notification(self, task: dict):
        if self.channel is None:
            return
        embed = build_task_embed(task)
        view = ApprovalView(task_id=task["id"], repo=task["repo"], pr_number=task["pr_number"])
        await self.channel.send(embed=embed, view=view)

    async def send_agent_complete(self, task_id: str, result: dict, view=None):
        if self.channel is None:
            return
        await self.channel.send(embed=build_agent_result_embed(result), view=view)

    async def send_gate_result(self, task_id: str, result: dict, gate: dict):
        if self.channel is None:
            return
        pr_url = result.get("pr_url")
        verdict = gate.get("verdict")
        if verdict == "passed":
            embed = build_agent_result_embed(result)
            embed.add_field(name="Quality gate", value=(gate.get("summary") or "")[:1000], inline=False)
            view = ReviewView(task_id=task_id, pr_url=pr_url)
        elif verdict == "partial":
            embed = build_needs_review_embed(gate, result)
            view = PartialGateView(task_id=task_id, pr_url=pr_url)
        else:
            embed = build_validation_failed_embed(gate, result)
            view = FailedGateView(task_id=task_id, pr_url=pr_url)
        await self.channel.send(embed=embed, view=view)

    async def send_verification_result(self, task_id: str, verification: dict):
        if self.channel is None:
            return
        await self.channel.send(embed=build_verification_embed(verification))

    async def on_message(self, message: discord.Message):
        if message.author.id != int(settings.DISCORD_OWNER_ID):
            return
        await self.process_commands(message)
        from app.discord.conversation import handle_message

        await handle_message(self, message)


bot = SentinelBot()
