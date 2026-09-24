"""Cursor Cloud Agents over REST. One httpx client, no local bridge."""

from __future__ import annotations

import httpx

from app.config import settings

_STATUS = {
    "FINISHED": "completed",
    "ERROR": "failed",
    "EXPIRED": "expired",
    "RUNNING": "running",
    "CREATING": "running",
    "CANCELLED": "cancelled",
    "STOPPED": "cancelled",
}


def public_status(status: str | None) -> str:
    if not status:
        return "running"
    return _STATUS.get(status, _STATUS.get(status.upper(), status.lower()))


def model_label(model: str | None) -> str:
    if not model or model == "auto":
        return "auto (Cursor picks)"
    return model


def _repo_url(full_name: str) -> str:
    if full_name.startswith("http"):
        return full_name
    return f"https://github.com/{full_name}"


def launch_body(repo_full_name: str, prompt: str, model: str | None, branch: str | None) -> dict:
    """Omit model when it is auto. Cursor then uses the account default."""
    text = prompt or ""
    if branch:
        text += (
            f"\n\nPush the fix on branch `{branch}` and open a pull request. "
            "Include [pr-sentinel] in the pull request body."
        )
    body = {
        "prompt": {"text": text or "Continue."},
        "source": {"repository": _repo_url(repo_full_name)},
        "target": {"autoCreatePr": True, "skipReviewerRequest": True},
    }
    if branch:
        body["target"]["branchName"] = branch
    if model and model != "auto":
        body["model"] = model
    return body


class CursorClient:
    def __init__(self):
        self.api_key = settings.CURSOR_API_KEY
        self.default_model = settings.CURSOR_DEFAULT_MODEL
        self._client = httpx.AsyncClient(
            base_url="https://api.cursor.com/v0",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

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
        response = await self._client.post(
            "/agents",
            json=launch_body(repo_full_name, prompt, model, branch),
        )
        response.raise_for_status()
        data = response.json()
        target = data.get("target") or {}
        return {
            "agent_id": data["id"],
            "status": public_status(data.get("status")),
            "model": "auto" if not model or model == "auto" else model,
            "repo": repo_full_name,
            "branch": target.get("branchName"),
            "pr_url": target.get("prUrl"),
        }

    async def get_run_status(self, agent_id: str) -> dict:
        response = await self._client.get(f"/agents/{agent_id}")
        response.raise_for_status()
        data = response.json()
        target = data.get("target") or {}
        return {
            "status": public_status(data.get("status")),
            "result_text": data.get("summary"),
            "branch": target.get("branchName"),
            "pr_url": target.get("prUrl"),
            "token_usage": None,
        }

    async def cancel_agent(self, agent_id: str) -> bool:
        response = await self._client.post(f"/agents/{agent_id}/stop")
        response.raise_for_status()
        return True

    async def list_models(self) -> list[str]:
        response = await self._client.get("/models")
        response.raise_for_status()
        models = response.json().get("models") or []
        return [m if isinstance(m, str) else m["id"] for m in models]

    async def resume_agent(self, agent_id: str, message: str | None = None) -> dict:
        response = await self._client.post(
            f"/agents/{agent_id}/followup",
            json={"prompt": {"text": message or "Continue with the previous task."}},
        )
        response.raise_for_status()
        return {"status": "running"}


cursor = CursorClient()
