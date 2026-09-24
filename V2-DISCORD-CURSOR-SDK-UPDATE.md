# PR Sentinel v2 — Mobile-First Discord + Cursor SDK Integration

> **Purpose:** Update MD for Cursor to add the Discord-driven, phone-operable remediation loop on top of v1.  
> **Key discovery:** Cursor has an official Python SDK (`cursor-sdk`) with full programmatic control — create cloud agents, choose models, stream events, cancel runs, get branches/PRs. No hacks needed.  
> **Do not delete existing v1 code.** Add new modules alongside.

---

## What Changes

v1: PR detected → agent diagnoses → posts GitHub comment → extension delivers task → dev fixes at desk.

v2: PR detected → agent diagnoses → **Discord notification with approve/reject buttons** → user approves from phone → **Cursor Cloud Agent launched via SDK with crafted prompt** → agent fixes in cloud VM → PR created → **user reviews diff in Discord** → **merges from Discord** → PR Sentinel verifies. Laptop never touched.

---

## New Dependencies

### Python (`backend/requirements.txt` — add these)

```
cursor-sdk>=0.1.0          # Cursor official Python SDK
discord.py>=2.4.0           # Discord bot
```

### Environment Variables (add to `.env.example` and `.env`)

```env
# ─── Discord ───
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_CHANNEL_ID=your_channel_id           # Channel where notifications go
DISCORD_GUILD_ID=your_guild_id               # Server ID (for slash commands)

# ─── Cursor SDK ───
CURSOR_API_KEY=crsr_your_api_key             # From Cursor Dashboard → API Keys
CURSOR_DEFAULT_MODEL=claude-sonnet-4-20250514   # Default model for cloud agents
```

### Discord Bot Setup (Manual — One Time)

1. Go to https://discord.com/developers/applications
2. New Application → name "PR Sentinel"
3. Bot tab → create bot → copy token → save as DISCORD_BOT_TOKEN
4. OAuth2 → URL Generator → scopes: `bot`, `applications.commands`
5. Bot permissions: `Send Messages`, `Embed Links`, `Add Reactions`, `Use Slash Commands`, `Attach Files`
6. Copy the generated URL → open in browser → add bot to your server
7. Get the channel ID: Discord settings → Advanced → enable Developer Mode → right-click channel → Copy Channel ID

### Cursor API Key (Manual — One Time)

1. Go to https://cursor.com → Dashboard → API Keys
2. Create a new API key
3. Save as CURSOR_API_KEY in .env

---

## Architecture: What Talks to What

```
GitHub Webhook
    │
    ▼
PR Sentinel Backend (FastAPI)
    │
    ├── Agent: DeepSeek (reasoning) + Jev (decisions)
    │   └── Produces: diagnosis, triage, Composer prompt
    │
    ├──── Discord Bot ◄──────────────────────────────┐
    │     │                                           │
    │     ├─ Sends notification with diagnosis        │
    │     ├─ Buttons: [Approve] [Reject] [Details]    │
    │     │                                           │
    │     ├─ On Approve:                              │
    │     │   └── Cursor Python SDK                   │
    │     │       └── Launch Cloud Agent              │
    │     │           ├── Model: user's choice        │
    │     │           ├── Prompt: crafted prompt       │
    │     │           ├── Repo: auto-detected          │
    │     │           └── Streams events → Discord     │
    │     │                                           │
    │     ├─ On Agent Complete:                       │
    │     │   ├── Shows diff in Discord               │
    │     │   └── Buttons: [Merge] [Reject] [Re-run]  │
    │     │                                           │
    │     ├─ Slash Commands:                          │
    │     │   ├── /status — current agent status      │
    │     │   ├── /stop — cancel running agent        │
    │     │   ├── /resume — re-launch same prompt     │
    │     │   ├── /model <name> — change model        │
    │     │   ├── /diff — show current diff           │
    │     │   ├── /merge — merge the fix PR           │
    │     │   ├── /reject — close PR, dismiss task    │
    │     │   ├── /logs — show agent trace            │
    │     │   └── /config — show current settings     │
    │     │                                           │
    │     └─ On Merge:                                │
    │         └── GitHub API: merge PR ───────────────┘
    │                │
    │                ▼
    └──── VERIFY phase (Jev) → ✅ resolved
```

---

## New Files to Create

```
backend/
├── app/
│   ├── discord/
│   │   ├── __init__.py
│   │   ├── bot.py                  # Discord bot client, event handlers
│   │   ├── commands.py             # Slash command definitions
│   │   ├── views.py               # Button views (Approve/Reject/Merge etc.)
│   │   ├── embeds.py              # Rich embed builders for notifications
│   │   └── cursor_bridge.py       # Cursor SDK integration
│   ├── services/
│   │   └── cursor_client.py       # Cursor Python SDK wrapper
│   └── services/
│       └── prompt_crafter.py      # DeepSeek-powered prompt engineering
```

---

## File Specifications

### 1. Cursor SDK Client (`backend/app/services/cursor_client.py`)

```python
"""
Wrapper around the official Cursor Python SDK.
Handles: agent creation, model selection, run control, result retrieval.
"""

from cursor_sdk import Agent, CloudAgentOptions, CloudRepository
from app.config import settings
import asyncio

class CursorClient:
    def __init__(self):
        self.api_key = settings.CURSOR_API_KEY
        self.default_model = settings.CURSOR_DEFAULT_MODEL

    async def list_models(self) -> list[str]:
        """List available models from the Cursor account."""
        # Use the SDK to enumerate models
        # Returns: ["claude-sonnet-4-20250514", "gpt-4o", "gemini-2.5-pro", ...]
        ...

    async def list_repos(self) -> list[dict]:
        """List GitHub repos connected to the Cursor account."""
        repos = CloudRepository.list(api_key=self.api_key)
        return [{"name": r.name, "full_name": r.full_name, "id": r.id} for r in repos]

    async def launch_agent(
        self,
        repo_full_name: str,
        prompt: str,
        model: str = None,
        branch: str = None,
    ) -> dict:
        """
        Launch a Cursor Cloud Agent to fix the code.
        
        Returns {
            "agent_id": "bc-xxxx",
            "status": "running",
            "model": "claude-sonnet-4-20250514",
            "repo": "AmartyaKumar11/pr-sentinel-demo"
        }
        """
        model = model or self.default_model

        agent = Agent.create(
            model=model,
            api_key=self.api_key,
            cloud=CloudAgentOptions(
                repository=repo_full_name,
                branch=branch,  # branch to work on, or None for new branch
            ),
        )

        run = agent.send(prompt)
        
        return {
            "agent_id": agent.id,
            "run_id": run.id if hasattr(run, 'id') else None,
            "status": "running",
            "model": model,
            "repo": repo_full_name,
        }

    async def stream_events(self, agent_id: str):
        """
        Async generator that yields agent events as they happen.
        Yields: {"type": "text"|"tool_call"|"status", "content": "..."}
        """
        # Reconnect to the agent and stream
        agent = Agent.reconnect(agent_id, api_key=self.api_key)
        for event in agent.current_run.stream():
            if hasattr(event, 'text'):
                yield {"type": "text", "content": event.text}
            elif hasattr(event, 'tool_name'):
                yield {"type": "tool_call", "content": f"{event.tool_name}: {event.tool_input}"}
            elif hasattr(event, 'status'):
                yield {"type": "status", "content": event.status}
        ...

    async def get_run_status(self, agent_id: str) -> dict:
        """
        Get the current status of a running agent.
        Returns {status, result_text, branch, pr_url, token_usage}
        """
        agent = Agent.reconnect(agent_id, api_key=self.api_key)
        run = agent.current_run
        return {
            "status": run.status,  # "running", "completed", "failed", "cancelled"
            "result_text": getattr(run, 'result_text', None),
            "branch": getattr(run, 'branch', None),
            "pr_url": getattr(run, 'pr_url', None),
            "token_usage": getattr(run, 'token_usage', None),
        }

    async def cancel_agent(self, agent_id: str) -> bool:
        """Cancel a running agent."""
        agent = Agent.reconnect(agent_id, api_key=self.api_key)
        agent.cancel()
        return True

    async def resume_agent(self, agent_id: str, message: str = None) -> dict:
        """Resume or send a follow-up to an existing agent."""
        agent = Agent.reconnect(agent_id, api_key=self.api_key)
        run = agent.send(message or "Continue with the previous task.")
        return {"status": "running", "run_id": run.id if hasattr(run, 'id') else None}
```

### 2. Prompt Crafter (`backend/app/services/prompt_crafter.py`)

```python
"""
Uses DeepSeek V4 Flash to generate high-quality Cursor Composer prompts.
The v1 template was static. This version uses a SHORT DeepSeek call to
craft a context-rich, actionable prompt that Cursor's agent can execute
effectively.

Jev pre-screens the diagnosis to decide prompt complexity:
- TRIVIAL/LOW → simple template (no DeepSeek call, save tokens)
- MEDIUM → template + one DeepSeek call for fix approach
- HIGH/CRITICAL → full DeepSeek-crafted prompt with constraints and verification steps
"""

from app.services.llm_client import LLMClient
from app.services.jev_client import JevClient

PROMPT_CRAFT_SYSTEM = """You are an expert at writing prompts for AI coding agents.
Given a PR diagnosis, write a prompt that a Cursor Cloud Agent will execute to fix the code.

RULES FOR THE PROMPT:
1. Start with a one-sentence summary of what needs to be fixed.
2. List each requirement that is missing, with the EXACT file and function that needs to change.
3. For each fix, describe the approach in 2-3 sentences. Do NOT write the code — describe what the code should do.
4. List constraints: don't break existing tests, don't modify unrelated files, match the repo's code style.
5. End with verification steps: what tests to run, what to check.
6. Keep the total prompt under 800 words — Cursor agents work better with focused instructions.
7. Use markdown formatting with clear headers.

DO NOT include generic boilerplate. Every sentence must be actionable."""

async def craft_prompt(diagnosis: dict, triage: dict, jev_client: JevClient = None, deepseek: LLMClient = None) -> str:
    """
    Generate a Cursor Composer prompt from the diagnosis.
    Uses Jev to decide complexity level, DeepSeek for the actual crafting.
    """
    severity = triage.get("severity", "MEDIUM")

    # For TRIVIAL/LOW: use a fast template, no LLM call
    if severity in ("TRIVIAL", "LOW"):
        return _simple_template(diagnosis, triage)

    # For MEDIUM/HIGH/CRITICAL: DeepSeek crafts a detailed prompt
    context = {
        "severity": severity,
        "missing_requirements": diagnosis.get("intent_alignment", {}).get("missing", []),
        "scope_creep": diagnosis.get("intent_alignment", {}).get("scope_creep", []),
        "addressed": diagnosis.get("intent_alignment", {}).get("addressed", []),
        "changed_files": diagnosis.get("changed_files", []),
        "blast_radius": {
            "risk_score": diagnosis.get("blast_radius", {}).get("risk_score", 0),
            "highest_risk_path": diagnosis.get("blast_radius", {}).get("highest_risk_path", ""),
            "untested": diagnosis.get("blast_radius", {}).get("untested_impacted", []),
        },
        "issue_title": diagnosis.get("linked_issue", {}).get("title", ""),
        "issue_requirements": diagnosis.get("linked_issue", {}).get("requirements", []),
        "suggested_fix": triage.get("suggested_fix_approach", ""),
        "affected_files": triage.get("affected_files_priority", []),
    }

    user_msg = f"""Craft a Cursor Cloud Agent prompt for this PR fix:

{__import__('json').dumps(context, indent=2)}

The agent will be launched on the repository with full codebase access.
It should create a fix branch and open a pull request when done."""

    response = await deepseek.chat(PROMPT_CRAFT_SYSTEM, [{"role": "user", "content": user_msg}])
    return response


def _simple_template(diagnosis: dict, triage: dict) -> str:
    """Fast template for trivial/low severity — no LLM call."""
    files = ", ".join(f.get("path", "?") for f in triage.get("affected_files_priority", []))
    return f"""Fix the following in {files}:

{triage.get('suggested_fix_approach', 'Address the flagged issues.')}

Constraints: don't break existing tests, match code style.
Open a PR when done."""
```

### 3. Discord Bot (`backend/app/discord/bot.py`)

```python
"""
Discord bot that runs alongside the FastAPI server.
Handles: notifications, button interactions, slash commands.
"""

import discord
from discord.ext import commands
from app.config import settings
from app.discord.commands import setup_commands
from app.discord.views import ApprovalView, ReviewView

class SentinelBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.channel = None

    async def setup_hook(self):
        """Called when bot is ready. Register slash commands."""
        await setup_commands(self)
        guild = discord.Object(id=int(settings.DISCORD_GUILD_ID))
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        self.channel = self.get_channel(int(settings.DISCORD_CHANNEL_ID))
        print(f"PR Sentinel bot online in #{self.channel.name}")

    async def send_task_notification(self, task: dict):
        """
        Send a rich notification for a new task.
        Includes: severity, missing requirements, blast radius, approve/reject buttons.
        """
        from app.discord.embeds import build_task_embed
        embed = build_task_embed(task)
        view = ApprovalView(task_id=task["id"], repo=task["repo"], pr_number=task["pr_number"])
        await self.channel.send(embed=embed, view=view)

    async def send_agent_complete(self, task_id: str, result: dict):
        """
        Send notification that Cursor agent finished.
        Includes: branch, PR URL, merge/reject buttons.
        """
        from app.discord.embeds import build_agent_result_embed
        embed = build_agent_result_embed(result)
        view = ReviewView(task_id=task_id, pr_url=result.get("pr_url"))
        await self.channel.send(embed=embed, view=view)

    async def send_verification_result(self, task_id: str, verification: dict):
        """Send notification that VERIFY phase completed."""
        from app.discord.embeds import build_verification_embed
        embed = build_verification_embed(verification)
        await self.channel.send(embed=embed)


# Global bot instance — started in FastAPI lifespan
bot = SentinelBot()
```

### 4. Discord Button Views (`backend/app/discord/views.py`)

```python
"""
Interactive button views for Discord messages.
Each button triggers an action in the PR Sentinel backend.
"""

import discord
from app.services.cursor_client import CursorClient
from app.services.prompt_crafter import craft_prompt
from app.services.task_manager import get_task, update_task_status
from app.database import get_db

cursor = CursorClient()

class ApprovalView(discord.ui.View):
    """Shown on new task notification. Approve launches Cursor agent."""

    def __init__(self, task_id: str, repo: str, pr_number: int):
        super().__init__(timeout=None)  # Persist across bot restarts
        self.task_id = task_id
        self.repo = repo
        self.pr_number = pr_number

    @discord.ui.button(label="Approve Fix", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)

        db = await get_db()
        task = await get_task(db, self.task_id)
        if not task:
            await interaction.followup.send("Task not found.", ephemeral=True)
            return

        # Update status
        await update_task_status(db, self.task_id, "accepted")

        # Launch Cursor Cloud Agent
        prompt = task.get("composer_prompt", "")
        result = await cursor.launch_agent(
            repo_full_name=self.repo,
            prompt=prompt,
            branch=f"pr-sentinel/fix-{self.pr_number}",
        )

        # Store agent_id on the task for status tracking
        await db.execute(
            "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
            (result["agent_id"], self.task_id)
        )
        await db.commit()

        await interaction.followup.send(
            f"🚀 Cursor Cloud Agent launched!\n"
            f"**Agent ID:** `{result['agent_id']}`\n"
            f"**Model:** {result['model']}\n"
            f"**Repo:** {result['repo']}\n\n"
            f"Use `/status` to check progress, `/stop` to cancel."
        )

        # Disable buttons after approval
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        # Start background task to monitor agent and notify on completion
        import asyncio
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
        diag = __import__('json').loads(task.get("diagnosis_json", "{}"))
        missing = diag.get("intent_alignment", {}).get("missing", [])
        creep = diag.get("intent_alignment", {}).get("scope_creep", [])
        blast = diag.get("blast_radius", {})

        details = f"**Missing requirements:**\n"
        for m in missing:
            details += f"  ⚠️ {m}\n"
        details += f"\n**Scope creep:**\n"
        for s in creep:
            details += f"  🔀 {s}\n"
        details += f"\n**Blast radius:** risk {blast.get('risk_score', 0):.2f}\n"
        details += f"**Path:** `{blast.get('highest_risk_path', 'N/A')}`\n"
        details += f"**Untested:** {', '.join(blast.get('untested_impacted', [])) or 'all covered'}"

        await interaction.response.send_message(details, ephemeral=True)

    async def _monitor_agent(self, agent_id: str, channel):
        """Poll Cursor agent status until complete, then notify."""
        import asyncio
        while True:
            await asyncio.sleep(10)
            try:
                status = await cursor.get_run_status(agent_id)
                if status["status"] in ("completed", "failed", "cancelled"):
                    from app.discord.bot import bot
                    await bot.send_agent_complete(self.task_id, status)
                    break
            except Exception:
                break


class ReviewView(discord.ui.View):
    """Shown when Cursor agent completes. Merge/reject the fix PR."""

    def __init__(self, task_id: str, pr_url: str = None):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.pr_url = pr_url

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.green, emoji="🔀")
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)

        if not self.pr_url:
            await interaction.followup.send("No PR URL available.", ephemeral=True)
            return

        # Extract owner/repo/pr_number from PR URL
        # https://github.com/owner/repo/pull/N
        import re
        match = re.search(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)', self.pr_url)
        if not match:
            await interaction.followup.send("Could not parse PR URL.", ephemeral=True)
            return

        owner, repo, pr_num = match.group(1), match.group(2), int(match.group(3))

        from app.services.github_client import GitHubClient
        gh = GitHubClient()
        result = await gh.merge_pr(owner, repo, pr_num)

        if result.get("merged"):
            await interaction.followup.send(f"✅ PR #{pr_num} merged!")
            # Task will be resolved by the VERIFY phase when the merge webhook fires
        else:
            await interaction.followup.send(f"❌ Merge failed: {result.get('message', 'unknown error')}")

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Reject Fix", style=discord.ButtonStyle.red, emoji="🚫")
    async def reject_fix(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.pr_url:
            import re
            match = re.search(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)', self.pr_url)
            if match:
                from app.services.github_client import GitHubClient
                gh = GitHubClient()
                await gh.close_pr(match.group(1), match.group(2), int(match.group(3)))

        await interaction.response.send_message("Fix PR closed. Task re-dispatched for manual fix.", ephemeral=True)
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
            prompt=task.get("composer_prompt", ""),
        )

        await db.execute(
            "UPDATE tasks SET cursor_agent_id = ? WHERE id = ?",
            (result["agent_id"], self.task_id)
        )
        await db.commit()

        await interaction.followup.send(
            f"🔄 Re-launched agent `{result['agent_id']}` with model {result['model']}"
        )
```

### 5. Discord Slash Commands (`backend/app/discord/commands.py`)

```python
"""
Slash commands for controlling PR Sentinel from Discord.
"""

import discord
from discord import app_commands
from app.services.cursor_client import CursorClient
from app.services.task_manager import get_task, get_existing_task
from app.services.github_client import GitHubClient
from app.database import get_db

cursor = CursorClient()
gh = GitHubClient()

async def setup_commands(bot):
    
    @bot.tree.command(name="status", description="Check the current Cursor agent status")
    async def status(interaction: discord.Interaction):
        db = await get_db()
        # Find the most recent active task
        row = await db.execute(
            "SELECT id, cursor_agent_id, repo, pr_number, severity FROM tasks "
            "WHERE cursor_agent_id IS NOT NULL AND status NOT IN ('resolved','error','dismissed') "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No active agent.", ephemeral=True)
            return

        result = await cursor.get_run_status(dict(task)["cursor_agent_id"])
        await interaction.response.send_message(
            f"**Agent:** `{dict(task)['cursor_agent_id']}`\n"
            f"**Status:** {result['status']}\n"
            f"**Branch:** {result.get('branch', 'N/A')}\n"
            f"**PR:** {result.get('pr_url', 'not yet')}\n"
            f"**Tokens:** {result.get('token_usage', 'N/A')}"
        )

    @bot.tree.command(name="stop", description="Cancel the running Cursor agent")
    async def stop(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks "
            "WHERE cursor_agent_id IS NOT NULL AND status NOT IN ('resolved','error','dismissed') "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No active agent to stop.", ephemeral=True)
            return

        await cursor.cancel_agent(dict(task)["cursor_agent_id"])
        await interaction.response.send_message("⏹️ Agent cancelled.")

    @bot.tree.command(name="resume", description="Resume or re-send instructions to the agent")
    @app_commands.describe(message="Optional follow-up instructions")
    async def resume(interaction: discord.Interaction, message: str = None):
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks "
            "WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No agent to resume.", ephemeral=True)
            return

        result = await cursor.resume_agent(dict(task)["cursor_agent_id"], message)
        await interaction.response.send_message(f"▶️ Agent resumed. Status: {result['status']}")

    @bot.tree.command(name="model", description="Change the model for the next agent run")
    @app_commands.describe(name="Model name (e.g., claude-sonnet-4-20250514, gpt-4o)")
    async def model(interaction: discord.Interaction, name: str):
        cursor.default_model = name
        await interaction.response.send_message(f"Model set to `{name}` for next run.")

    @bot.tree.command(name="models", description="List available Cursor models")
    async def models(interaction: discord.Interaction):
        available = await cursor.list_models()
        model_list = "\n".join(f"  • `{m}`" for m in available)
        await interaction.response.send_message(f"**Available models:**\n{model_list}")

    @bot.tree.command(name="diff", description="Show the current fix diff")
    async def diff(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id FROM tasks "
            "WHERE cursor_agent_id IS NOT NULL "
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

        # Fetch the PR diff
        import re
        match = re.search(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)', pr_url)
        if match:
            diff_text = await gh.get_pr_diff(match.group(1), match.group(2), int(match.group(3)))
            # Truncate for Discord (2000 char limit)
            if len(diff_text) > 1900:
                diff_text = diff_text[:1900] + "\n... (truncated)"
            await interaction.response.send_message(f"```diff\n{diff_text}\n```")
        else:
            await interaction.response.send_message(f"PR: {pr_url}")

    @bot.tree.command(name="merge", description="Merge the fix PR")
    async def merge(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        db = await get_db()
        row = await db.execute(
            "SELECT cursor_agent_id, repo, pr_number FROM tasks "
            "WHERE cursor_agent_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.followup.send("No task with a PR to merge.")
            return

        result = await cursor.get_run_status(dict(task)["cursor_agent_id"])
        pr_url = result.get("pr_url")
        if not pr_url:
            await interaction.followup.send("No PR to merge yet.")
            return

        import re
        match = re.search(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)', pr_url)
        if match:
            merge_result = await gh.merge_pr(match.group(1), match.group(2), int(match.group(3)))
            if merge_result.get("merged"):
                await interaction.followup.send(f"✅ Merged! The VERIFY phase will run automatically.")
            else:
                await interaction.followup.send(f"❌ Merge failed: {merge_result.get('message')}")

    @bot.tree.command(name="logs", description="Show the agent reasoning trace")
    async def logs(interaction: discord.Interaction):
        db = await get_db()
        row = await db.execute(
            "SELECT id FROM tasks ORDER BY created_at DESC LIMIT 1"
        )
        task = await row.fetchone()
        if not task:
            await interaction.response.send_message("No tasks found.", ephemeral=True)
            return

        trace = await db.execute(
            "SELECT phase, type, content FROM trace_steps WHERE task_id = ? ORDER BY step_number",
            (dict(task)["id"],)
        )
        steps = await trace.fetchall()
        log_text = ""
        for s in steps[:15]:  # Cap at 15 for Discord limits
            s = dict(s)
            emoji = {"thought": "💭", "action": "🔧", "observation": "👁", "answer": "✅"}.get(s["type"], "•")
            content = s["content"][:100]
            log_text += f"{emoji} [{s['phase']}] {content}\n"

        if not log_text:
            log_text = "No trace steps recorded."

        await interaction.response.send_message(f"```\n{log_text}\n```")

    @bot.tree.command(name="config", description="Show current PR Sentinel configuration")
    async def config(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**Model:** `{cursor.default_model}`\n"
            f"**Backend:** `{settings.DEEPSEEK_BASE_URL}`\n"
            f"**LLM:** DeepSeek V4 Flash\n"
            f"**Decision engine:** Jev {settings.JEV_MODEL}\n"
            f"**Dashboard:** {settings.FRONTEND_URL}"
        )
```

### 6. Discord Embeds (`backend/app/discord/embeds.py`)

```python
"""
Rich embed builders for Discord notifications.
"""

import discord

SEVERITY_COLORS = {
    "TRIVIAL": 0x9CA3AF,   # gray
    "LOW": 0x22C55E,       # green
    "MEDIUM": 0xEAB308,    # yellow
    "HIGH": 0xF97316,      # orange
    "CRITICAL": 0xEF4444,  # red
}

def build_task_embed(task: dict) -> discord.Embed:
    """Build the notification embed for a new task."""
    import json
    severity = task.get("severity", "MEDIUM")
    diag = json.loads(task.get("diagnosis_json", "{}")) if isinstance(task.get("diagnosis_json"), str) else task.get("diagnosis_json", {})
    intent = diag.get("intent_alignment", {})
    blast = diag.get("blast_radius", {})
    jev = json.loads(task.get("jev_confidences", "{}")) if isinstance(task.get("jev_confidences"), str) else task.get("jev_confidences", {})

    embed = discord.Embed(
        title=f"{'🔴' if severity == 'CRITICAL' else '🟠' if severity == 'HIGH' else '🟡' if severity == 'MEDIUM' else '🟢'} PR #{task.get('pr_number')} — {severity}",
        description=task.get("suggested_fix", "Review needed."),
        color=SEVERITY_COLORS.get(severity, 0x9CA3AF),
    )

    # Missing requirements
    missing = intent.get("missing", [])
    if missing:
        embed.add_field(
            name="⚠️ Missing requirements",
            value="\n".join(f"• {m}" for m in missing[:5]),
            inline=False,
        )

    # Scope creep
    creep = intent.get("scope_creep", [])
    if creep:
        embed.add_field(
            name="🔀 Scope creep",
            value="\n".join(f"• {c}" for c in creep[:3]),
            inline=False,
        )

    # Blast radius
    embed.add_field(
        name="💥 Blast radius",
        value=f"Risk: {blast.get('risk_score', 0):.2f} | "
              f"Path: `{blast.get('highest_risk_path', 'N/A')}`",
        inline=False,
    )

    # Jev confidence
    if jev:
        embed.add_field(
            name="🤖 Jev confidence",
            value=f"Auth: {jev.get('touches_auth', 0):.0%} | "
                  f"Trivial: {jev.get('is_trivial', 0):.0%} | "
                  f"Risk: {jev.get('risk_level', 0):.1f}/3",
            inline=False,
        )

    embed.set_footer(text=f"Repo: {task.get('repo')} | Task: {task.get('id', '')[:8]}")
    return embed


def build_agent_result_embed(result: dict) -> discord.Embed:
    """Build embed for Cursor agent completion."""
    status = result.get("status", "unknown")
    color = 0x22C55E if status == "completed" else 0xEF4444

    embed = discord.Embed(
        title=f"{'✅' if status == 'completed' else '❌'} Cursor Agent {status.title()}",
        color=color,
    )

    if result.get("branch"):
        embed.add_field(name="Branch", value=f"`{result['branch']}`", inline=True)
    if result.get("pr_url"):
        embed.add_field(name="Pull Request", value=result["pr_url"], inline=True)
    if result.get("token_usage"):
        embed.add_field(name="Tokens used", value=str(result["token_usage"]), inline=True)
    if result.get("result_text"):
        text = result["result_text"][:500]
        embed.add_field(name="Summary", value=text, inline=False)

    return embed


def build_verification_embed(verification: dict) -> discord.Embed:
    """Build embed for VERIFY phase result."""
    all_resolved = verification.get("all_resolved", False)
    color = 0x22C55E if all_resolved else 0xEAB308

    embed = discord.Embed(
        title=f"{'✅' if all_resolved else '⚠️'} Verification {'Passed' if all_resolved else 'Partial'}",
        color=color,
    )

    if verification.get("resolved_items"):
        embed.add_field(
            name="Resolved",
            value="\n".join(f"✅ {r}" for r in verification["resolved_items"]),
            inline=False,
        )
    if verification.get("remaining_items"):
        embed.add_field(
            name="Still missing",
            value="\n".join(f"⚠️ {r}" for r in verification["remaining_items"]),
            inline=False,
        )

    return embed
```

### 7. Integration: Start Bot with FastAPI (`backend/app/main.py` — patch)

```python
# Add to FastAPI lifespan

from contextlib import asynccontextmanager
import asyncio

@asynccontextmanager
async def lifespan(app):
    # Start Discord bot in background
    from app.discord.bot import bot
    bot_task = asyncio.create_task(bot.start(settings.DISCORD_BOT_TOKEN))

    yield

    # Shutdown
    await bot.close()
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)
```

### 8. Integration: Dispatch → Discord Notification (patch orchestrator)

```python
# In _run_dispatch, AFTER posting GitHub comment and updating status to dispatched:

from app.discord.bot import bot

# Send Discord notification
await bot.send_task_notification({
    "id": task_id,
    "repo": f"{owner}/{repo}",
    "pr_number": pr_number,
    "severity": triage["severity"],
    "suggested_fix": triage.get("suggested_fix_approach", ""),
    "diagnosis_json": json.dumps(diagnosis),
    "jev_confidences": json.dumps(triage.get("confidence_scores", {})),
})
```

### 9. Database Patch — Add cursor_agent_id column

```sql
ALTER TABLE tasks ADD COLUMN cursor_agent_id TEXT;
-- Also add to the CREATE TABLE IF NOT EXISTS in database.py
```

### 10. GitHub Client Patch — Add merge_pr and close_pr

```python
# Add to backend/app/services/github_client.py

async def merge_pr(self, owner: str, repo: str, pr_number: int, merge_method: str = "squash") -> dict:
    """Merge a PR via GitHub API."""
    response = await self._client.put(
        f"{self.base}/repos/{owner}/{repo}/pulls/{pr_number}/merge",
        json={"merge_method": merge_method}
    )
    return response.json()

async def close_pr(self, owner: str, repo: str, pr_number: int) -> dict:
    """Close a PR without merging."""
    response = await self._client.patch(
        f"{self.base}/repos/{owner}/{repo}/pulls/{pr_number}",
        json={"state": "closed"}
    )
    return response.json()
```

---

## Jev Integration Points in v2

Jev isn't just for triage anymore. v2 adds these decision points:

```python
# 1. AUTO-APPROVE ROUTING (in dispatch)
# Should this task auto-approve (skip Discord confirmation)?
auto_approve_check = jev.evaluate(
    state={"severity": triage["severity"], "risk_score": ..., "missing_count": ...},
    questions={
        "auto_approvable": Noul(
            instructions="This fix is low-risk enough to auto-approve without human confirmation. "
                        "Only true for LOW severity with risk_score under 0.2 and no missing requirements."
        )
    }
)
if auto_approve_check["auto_approvable"].noul > 0.85:
    # Skip Discord approval, launch Cursor agent immediately
    ...
else:
    # Send to Discord for human approval
    ...

# 2. PROMPT COMPLEXITY ROUTING (in prompt_crafter)
# Already described in prompt_crafter.py — Jev decides template vs DeepSeek-crafted

# 3. AGENT RESULT QUALITY CHECK (after Cursor agent completes)
# Before showing merge button, Jev evaluates the PR
quality_check = jev.evaluate(
    state={"fix_diff": diff_text, "original_missing": missing_reqs},
    questions={
        "addresses_all_requirements": Noul(
            instructions="The fix diff addresses all the originally missing requirements"
        ),
        "introduces_new_issues": Noul(
            instructions="The fix introduces obvious new bugs or breaks existing functionality"
        ),
    }
)
# If quality is low, warn in Discord before showing merge button
```

---

## Complete Phone Flow (What Amartya Experiences)

```
1. Amartya pushes a PR from laptop (or someone else does)

2. Phone buzzes — Discord notification:
   ┌─────────────────────────────────┐
   │ 🔴 PR #8 — CRITICAL             │
   │                                  │
   │ Add email validation before      │
   │ generating token. Add token      │
   │ expiry.                          │
   │                                  │
   │ ⚠️ Missing: validate email,      │
   │    expire token                  │
   │ 💥 Risk: 0.65                    │
   │                                  │
   │ [✅ Approve] [❌ Reject] [📋 Details] │
   └─────────────────────────────────┘

3. Amartya taps [✅ Approve]
   → Discord: "🚀 Cursor Cloud Agent launched! Model: claude-sonnet-4"

4. Amartya types /status after a minute
   → Discord: "Status: running, Branch: pr-sentinel/fix-8"

5. Two minutes later, phone buzzes again:
   ┌─────────────────────────────────┐
   │ ✅ Cursor Agent Completed        │
   │                                  │
   │ Branch: pr-sentinel/fix-8        │
   │ PR: github.com/.../pull/9        │
   │ Tokens: 12,847                   │
   │                                  │
   │ [🔀 Merge] [🚫 Reject] [🔄 Re-run] │
   └─────────────────────────────────┘

6. Amartya types /diff to see what changed
   → Discord shows the diff in a code block

7. Amartya taps [🔀 Merge]
   → Discord: "✅ PR #9 merged!"

8. PR Sentinel VERIFY fires on the merge webhook:
   ┌─────────────────────────────────┐
   │ ✅ Verification Passed           │
   │                                  │
   │ ✅ Validate email format          │
   │ ✅ Expire token after 1 hour      │
   │                                  │
   │ All issues resolved.             │
   └─────────────────────────────────┘

Total time: ~3 minutes. Laptop never opened.
```

---

## Summary of Changes for Cursor

| File | Action | What |
|---|---|---|
| `requirements.txt` | Append | `cursor-sdk`, `discord.py` |
| `.env.example` | Append | Discord + Cursor SDK vars |
| `app/config.py` | Patch | Add Discord + Cursor settings |
| `app/services/cursor_client.py` | Create | Cursor SDK wrapper |
| `app/services/prompt_crafter.py` | Create | DeepSeek prompt engineering |
| `app/services/github_client.py` | Patch | Add `merge_pr`, `close_pr` |
| `app/discord/__init__.py` | Create | Package init |
| `app/discord/bot.py` | Create | Discord bot client |
| `app/discord/commands.py` | Create | 9 slash commands |
| `app/discord/views.py` | Create | Button views (Approve, Review) |
| `app/discord/embeds.py` | Create | Rich notification embeds |
| `app/main.py` | Patch | Start bot in lifespan |
| `app/agent/orchestrator.py` | Patch | Send Discord notification in dispatch |
| `app/database.py` | Patch | Add `cursor_agent_id` column to tasks |
| `app/models/schemas.py` | Patch | Add `cursor_agent_id` to Task schema |
