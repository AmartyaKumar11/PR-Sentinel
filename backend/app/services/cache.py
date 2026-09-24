"""SQLite-backed async cache (BUILD-SEQUENCE Phase 1.5)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import get_db


async def cache_get(key: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT value, expires_at FROM cache WHERE cache_key = ?", (key,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    if row[1] and row[1] < datetime.now(timezone.utc).isoformat():
        await db.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
        await db.commit()
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_set(key: str, value: dict, expires_at: str | None = None) -> None:
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT OR REPLACE INTO cache (cache_key, value, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (key, json.dumps(value), now, expires_at),
    )
    await db.commit()
