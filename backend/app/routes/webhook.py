import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agent.orchestrator import AgentOrchestrator
from app.config import settings
from app.database import get_db
from app.services.task_manager import get_existing_task
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
    full = payload["repository"]["full_name"]
    owner, repo = full.split("/", 1)
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]

    db = await get_db()
    existing = await get_existing_task(db, full, pr_number)

    if action == "synchronize" and existing:
        task_id = existing["id"]
        mode = "verify"
    else:
        task_id = str(uuid.uuid4())
        mode = "full"

    asyncio.create_task(
        _agent.run(task_id, owner, repo, pr_number, head_sha, mode=mode)
    )

    return JSONResponse({"task_id": task_id}, status_code=202)
