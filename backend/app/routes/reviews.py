from fastapi import APIRouter, HTTPException, Query

from app.database import get_db
from app.services.task_manager import get_review_detail, get_reviews

router = APIRouter()


@router.get("/api/reviews")
async def list_reviews(
    repo: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    db = await get_db()
    reviews, total = await get_reviews(db, repo, limit, offset)
    return {"reviews": reviews, "total": total}


@router.get("/api/reviews/{task_id}")
async def review_detail(task_id: str):
    db = await get_db()
    detail = await get_review_detail(db, task_id)
    if not detail:
        raise HTTPException(404, "Task not found")
    return detail
