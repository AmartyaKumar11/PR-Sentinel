import pytest
import aiosqlite

from app.database import _SCHEMA
from app.services.task_manager import create_task, update_task_status


async def _db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    return db


@pytest.mark.asyncio
async def test_pending_to_dispatched_ok():
    db = await _db()
    await create_task(db, "t1", "o/r", 1, "abc", "HIGH", "dispatch", {}, {}, "md", "prompt")
    ok = await update_task_status(db, "t1", "dispatched")
    assert ok
    row = await (await db.execute("SELECT status FROM tasks WHERE id='t1'")).fetchone()
    assert row["status"] == "dispatched"
    await db.close()


@pytest.mark.asyncio
async def test_accept_task_from_pending():
    from app.services.task_manager import accept_task

    db = await _db()
    await create_task(db, "t3", "o/r", 3, "abc", "TRIVIAL", "skip", {}, {}, "md", "prompt")
    await accept_task(db, "t3")
    row = await (await db.execute("SELECT status, accepted_at FROM tasks WHERE id='t3'")).fetchone()
    assert row["status"] == "accepted"
    assert row["accepted_at"]
    await db.close()


@pytest.mark.asyncio
async def test_resolved_to_accepted_rejected():
    db = await _db()
    await create_task(db, "t2", "o/r", 2, "def", "LOW", "skip", {}, {}, "md", "prompt")
    await update_task_status(db, "t2", "dispatched")
    await update_task_status(db, "t2", "resolved")
    with pytest.raises(ValueError):
        await update_task_status(db, "t2", "accepted")
    await db.close()
