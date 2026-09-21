import time

from fastapi import APIRouter

from app.config import settings
from app.database import get_db
from app.services.task_manager import get_health_stats

router = APIRouter()
_started_at = time.time()


@router.get("/api/health")
async def health():
    db = await get_db()
    stats = await get_health_stats(db)
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _started_at),
        "reviews_completed": stats["reviews_completed"],
        "last_webhook_at": stats["last_webhook_at"],
        "llm_provider": settings.LLM_MODEL,
        "cache_size_kb": stats["cache_size_kb"],
    }
