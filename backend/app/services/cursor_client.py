"""Cursor cloud agents via the official cursor-sdk."""

from __future__ import annotations

import asyncio
import logging

from cursor_sdk import (
    Agent,
    AgentOptions,
    CloudAgentOptions,
    CloudRepository,
    CursorClient as SdkClient,
)

from app.config import settings

logger = logging.getLogger(__name__)

_STATUS = {"finished": "completed", "error": "failed"}


def public_status(status: str) -> str:
    return _STATUS.get(status, status)


def model_label(model: str | None) -> str:
    if not model or model == "auto":
        return "auto (Cursor picks)"
    return model


def agent_create_kwargs(model: str | None, api_key: str, cloud) -> dict:
    """Omit model when it is auto so Agent.create does not get a guessed id."""
    kwargs = {"api_key": api_key, "cloud": cloud}
    if model and model != "auto":
        kwargs["model"] = model
    return kwargs


def _repo_url(full_name: str) -> str:
    if full_name.startswith("http"):
        return full_name
    return f"https://github.com/{full_name}"


def _git_bits(run) -> tuple[str | None, str | None]:
    git = getattr(run, "git", None)
    branches = getattr(git, "branches", None) or []
    if not branches:
        return None, None
    return branches[0].branch or None, branches[0].pr_url or None


class CursorClient:
    def __init__(self):
        self.api_key = settings.CURSOR_API_KEY
        self.default_model = settings.CURSOR_DEFAULT_MODEL

    async def list_models(self) -> list[str]:
        def _list() -> list[str]:
            with SdkClient.launch_bridge() as client:
                return [m.id for m in client.list_models(api_key=self.api_key)]

        return await asyncio.to_thread(_list)

    async def list_repos(self) -> list[dict]:
        def _list() -> list[dict]:
            with SdkClient.launch_bridge() as client:
                out = []
                for repo in client.list_repositories(api_key=self.api_key):
                    url = repo.url
                    full = url.removeprefix("https://github.com/").removesuffix(".git")
                    out.append({"name": full.split("/")[-1], "full_name": full, "id": url})
                return out

        return await asyncio.to_thread(_list)

    async def launch_agent(
        self,
        repo_full_name: str,
        prompt: str,
        model: str | None = None,
        branch: str | None = None,
    ) -> dict:
        model = model or self.default_model
        if not self.api_key:
            raise RuntimeError("CURSOR_API_KEY is not set")
        cloud = CloudAgentOptions(
            repos=[CloudRepository(url=_repo_url(repo_full_name))],
            auto_create_pr=True,
            skip_reviewer_request=True,
        )
        kwargs = agent_create_kwargs(model, self.api_key, cloud)

        def _launch() -> dict:
            # ponytail: do not agent.close() — CloseAgent drops the cloud run.
            agent = Agent.create(**kwargs)
            text = prompt
            if branch:
                text += f"\n\nPush the fix on branch `{branch}` and open a pull request."
            run = agent.send(text)
            return {
                "agent_id": agent.agent_id or run.agent_id,
                "run_id": run.id or None,
                "status": "running",
                "model": model if model and model != "auto" else "auto",
                "repo": repo_full_name,
            }

        return await asyncio.to_thread(_launch)

    async def stream_events(self, agent_id: str):
        while True:
            status = await self.get_run_status(agent_id)
            yield {"type": "status", "content": status["status"]}
            if status["status"] in ("completed", "failed", "cancelled", "expired"):
                if status.get("result_text"):
                    yield {"type": "text", "content": status["result_text"]}
                break
            await asyncio.sleep(5)

    async def get_run_status(self, agent_id: str) -> dict:
        def _status() -> dict:
            with SdkClient.launch_bridge() as client:
                runs = client.list_runs(agent_id, api_key=self.api_key, limit=1)
                if not runs.items:
                    return {
                        "status": "running",
                        "result_text": None,
                        "branch": None,
                        "pr_url": None,
                        "token_usage": None,
                    }
                run = runs.items[0]
                branch, pr_url = _git_bits(run)
                return {
                    "status": public_status(run.status),
                    "result_text": run.result or None,
                    "branch": branch,
                    "pr_url": pr_url,
                    "token_usage": str(run.usage) if getattr(run, "usage", None) else None,
                }

        return await asyncio.to_thread(_status)

    async def cancel_agent(self, agent_id: str) -> bool:
        def _cancel() -> bool:
            with SdkClient.launch_bridge() as client:
                runs = client.list_runs(agent_id, api_key=self.api_key, limit=5)
                for run in runs.items:
                    if run.status == "running" and run.supports("cancel"):
                        run.cancel()
            return True

        return await asyncio.to_thread(_cancel)

    async def resume_agent(self, agent_id: str, message: str | None = None) -> dict:
        def _resume() -> dict:
            agent = Agent.resume(
                agent_id,
                AgentOptions(api_key=self.api_key),
            )
            run = agent.send(message or "Continue with the previous task.")
            return {"status": "running", "run_id": run.id or None}

        return await asyncio.to_thread(_resume)


cursor = CursorClient()
