import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agent.orchestrator import AgentOrchestrator
from app.config import settings
from app.database import get_db
from app.services.task_manager import (
    get_existing_task,
    get_pending_for_pr,
    save_pending,
    update_pending_sha,
)
from app.utils.hmac_verify import verify_hmac

logger = logging.getLogger(__name__)
router = APIRouter()
_agent = AgentOrchestrator()


@router.post("/api/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_hmac(body, signature, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(401, "Invalid webhook signature")

    payload = json.loads(body)
    event_type = request.headers.get("X-GitHub-Event", "")

    if event_type != "pull_request":
        return {"skipped": True, "reason": f"Event type: {event_type}"}

    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return {"skipped": True, "reason": f"Action: {action}"}

    pr = payload["pull_request"]
    if sentinel_pr(pr):
        return {"skipped": True, "reason": "PR created by PR Sentinel"}

    full = payload["repository"]["full_name"]
    owner, repo = full.split("/", 1)
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]

    db = await get_db()
    existing = await get_existing_task(db, full, pr_number)

    if action == "synchronize" and existing:
        asyncio.create_task(
            _agent.run(existing["id"], owner, repo, pr_number, head_sha, mode="verify")
        )
        return JSONResponse({"task_id": existing["id"]}, status_code=202)

    meta = _pr_meta(payload, full)
    pending = await get_pending_for_pr(db, full, pr_number)
    if pending:
        await update_pending_sha(db, pending["id"], head_sha)
        return JSONResponse({"pending_id": pending["id"]}, status_code=202)

    meta["id"] = str(uuid.uuid4())
    await save_pending(db, meta)
    asyncio.create_task(_notify_pr(meta))
    return JSONResponse({"pending_id": meta["id"]}, status_code=202)


def sentinel_pr(pr: dict) -> bool:
    """Skip fix PRs this bot asked Cursor to open. The GitHub token owner is the human."""
    branch = ((pr.get("head") or {}).get("ref") or "")
    body = (pr.get("body") or "").lower()
    author = ((pr.get("user") or {}).get("login") or "").lower()
    if branch.startswith("pr-sentinel/"):
        return True
    if "[pr-sentinel]" in body or "pr sentinel" in body:
        return True
    return author == "cursoragent" or author.endswith("[bot]")


def _pr_meta(payload: dict, full: str) -> dict:
    pr = payload["pull_request"]
    files = []
    for item in pr.get("files") or []:
        if isinstance(item, str):
            files.append(item)
        elif isinstance(item, dict) and item.get("filename"):
            files.append(item["filename"])
    if not files and pr.get("changed_files") is not None:
        files = [f"{pr['changed_files']} files"]
    user = pr.get("user") or {}
    return {
        "repo": full,
        "pr_number": pr["number"],
        "head_sha": pr["head"]["sha"],
        "title": pr.get("title") or "",
        "body": (pr.get("body") or "")[:300],
        "author": user.get("login") or "unknown",
        "files": files,
        "head_ref": (pr.get("head") or {}).get("ref") or "",
        "base_ref": (pr.get("base") or {}).get("ref") or "",
    }


async def _notify_pr(meta: dict) -> None:
    try:
        from app.discord.bot import bot

        if not bot.is_ready():
            logger.warning("discord not ready; raw PR notice skipped for #%s", meta["pr_number"])
            return
        await bot.send_pr_notice(meta)
    except Exception:
        logger.warning("discord raw PR notice failed", exc_info=True)
