from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import get_db
from app.services.task_manager import get_task, get_tasks, update_task_status

router = APIRouter()


class StatusUpdate(BaseModel):
    status: str
    extension_version: str | None = None
    editor: str | None = None


@router.get("/api/tasks")
async def list_tasks(
    repo: str = Query(...),
    status: str = Query("pending,dispatched"),
):
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    db = await get_db()
    tasks = await get_tasks(db, repo, statuses)
    return {"tasks": tasks}


@router.get("/api/tasks/{task_id}")
async def task_detail(task_id: str):
    db = await get_db()
    task = await get_task(db, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.patch("/api/tasks/{task_id}")
async def patch_task(task_id: str, body: StatusUpdate):
    if body.status not in ("accepted", "in_progress", "dismissed"):
        raise HTTPException(400, f"Invalid status from extension: {body.status}")
    db = await get_db()
    try:
        ok = await update_task_status(db, task_id, body.status)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not ok:
        raise HTTPException(404, "Task not found")
    return {"ok": True}
