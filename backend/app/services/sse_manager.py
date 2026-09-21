"""In-memory SSE fan-out for agent traces."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class SSEManager:
    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def emit(self, task_id: str, event: dict) -> None:
        for q in list(self._queues.get(task_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, task_id: str):
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues[task_id].append(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event.get("type") == "answer" and event.get("phase") == "dispatch":
                    break
                if event.get("type") == "error":
                    break
        finally:
            if q in self._queues.get(task_id, []):
                self._queues[task_id].remove(q)


sse_manager = SSEManager()
