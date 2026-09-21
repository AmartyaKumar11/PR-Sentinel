from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse
import json

from app.services.sse_manager import sse_manager

router = APIRouter()


@router.get("/api/stream/{task_id}")
async def stream_trace(task_id: str):
    async def event_generator():
        async for event in sse_manager.subscribe(task_id):
            yield {"data": json.dumps(event)}

    return EventSourceResponse(event_generator())
