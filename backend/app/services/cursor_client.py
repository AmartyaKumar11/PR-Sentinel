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


def _file_names(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    names = []
    for item in raw:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("path") or item.get("filename") or item.get("file") or ""
            if name:
                names.append(name)
    return names


def run_status_from(data: dict, messages: list | None = None) -> dict:
    """Keep activity fields the agent endpoint already returns."""
    target = data.get("target") or {}
    raw_files = data.get("filesChanged")
    if raw_files is None:
        raw_files = data.get("files_changed")
    if raw_files is None:
        raw_files = data.get("files_edited")
    files = _file_names(raw_files)
    last = ""
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or msg.get("type") == "user_message":
            continue
        text = msg.get("text") or msg.get("content") or ""
        if isinstance(text, str) and text.strip():
            last = " ".join(text.split())[:500]
            break
    return {
        "status": public_status(data.get("status")),
        "result_text": data.get("summary") or "",
        "name": data.get("name") or "",
        "branch": target.get("branchName"),
        "pr_url": target.get("prUrl"),
        "token_usage": data.get("tokenUsage") or data.get("token_usage"),
        "files_changed": files,
        "files_edited": files,
        "files_changed_count": raw_files if isinstance(raw_files, int) else (len(files) or None),
        "lines_added": data.get("linesAdded"),
        "lines_removed": data.get("linesRemoved"),
        "last_activity": last,
        "current_step": last,
    }


def activity_bits(status: dict) -> tuple[str, str]:
    files = status.get("files_edited") or status.get("files_changed") or []
    names = _file_names(files) if isinstance(files, list) else []
    if names:
        files_line = ", ".join(f"`{name}`" for name in names[-5:])
    elif isinstance(status.get("files_changed_count"), int):
        files_line = str(status["files_changed_count"])
    else:
        files_line = ""
    activity = status.get("current_step") or status.get("last_activity") or ""
    if not isinstance(activity, str):
        activity = ""
    return files_line, " ".join(activity.split())[:200]


def format_status_reply(agent_id: str, status: dict) -> str:
    files_line, activity = activity_bits(status)
    lines = [
        f"**Agent:** `{agent_id}`",
        f"**Status:** {status.get('status')}",
        f"**Branch:** `{status.get('branch') or 'creating...'}`",
    ]
    if files_line:
        label = "Files" if (status.get("files_changed") or status.get("files_edited")) else "Files changed"
        lines.append(f"**{label}:** {files_line}")
    if activity:
        lines.append(f"**Currently:** {activity}")
    lines.append(f"**PR:** {status.get('pr_url') or 'not yet'}")
    if status.get("token_usage"):
        lines.append(f"**Tokens:** {status['token_usage']}")
    return "\n".join(lines)


def format_progress(pr_number, status: dict) -> str:
    files_line, activity = activity_bits(status)
    text = (
        f"🔧 **Agent working on PR #{pr_number}**\n"
        f"Status: {status.get('status')}\n"
        f"Branch: `{status.get('branch') or 'creating...'}`\n"
    )
    if files_line:
        label = "Files touched" if (status.get("files_changed") or status.get("files_edited")) else "Files changed"
        text += f"{label}: {files_line}\n"
    if activity:
        text += f"\n> {activity}"
    if status.get("pr_url"):
        text += f"\nPR: {status['pr_url']}"
    return text[:1900]


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
        messages = []
        try:
            conv = await self._client.get(f"/agents/{agent_id}/conversation")
            if conv.status_code < 400:
                messages = (conv.json() or {}).get("messages") or []
        except Exception:
            messages = []
        return run_status_from(response.json(), messages)

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
