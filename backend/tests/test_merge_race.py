"""Merge-race guards: illegal transitions stay put, and Merge requires verification."""

import pytest

from app.agent.orchestrator import AgentOrchestrator
from app.database import get_db
from app.discord.views import _is_verified_for_merge
from app.services.task_manager import (
    IllegalTransitionError,
    accept_task,
    create_task,
    get_task,
    update_task_status,
)


async def _accepted(task_id: str, pr: int) -> None:
    db = await get_db()
    await create_task(
        db,
        task_id,
        "AmartyaKumar11/PR-Sentinel",
        pr,
        "aaa",
        "MEDIUM",
        "dispatch",
        {"blast_radius": {}, "intent_alignment": {"missing": [], "scope_creep": [], "addressed": []}},
        {"affected_files_priority": [], "suggested_fix_approach": "n/a"},
        "review",
        "prompt",
    )
    await accept_task(db, task_id)


@pytest.mark.asyncio
async def test_accepted_to_dispatched_raises_and_verify_does_not_mark_error(monkeypatch):
    assert issubclass(IllegalTransitionError, ValueError)
    await _accepted("race-transition", 88010)
    db = await get_db()
    with pytest.raises(IllegalTransitionError, match="accepted → dispatched"):
        await update_task_status(db, "race-transition", "dispatched")

    async def _trip(self, task_id, *_args, **_kwargs):
        await update_task_status(await get_db(), task_id, "dispatched")

    monkeypatch.setattr(AgentOrchestrator, "_run_jev_verify", _trip)
    await AgentOrchestrator().run("race-transition", "o", "r", 88010, "sha", mode="verify")

    task = await get_task(await get_db(), "race-transition")
    assert task["status"] == "accepted"
    assert not task["verification_json"]


def test_is_verified_for_merge_requires_all_three_columns():
    assert _is_verified_for_merge(
        {"is_verified": 1, "verification_json": '{"all_resolved": true}', "resolved_sha": "abc"}
    )
    assert not _is_verified_for_merge(
        {"status": "resolved", "is_verified": 1, "verification_json": None, "resolved_sha": "abc"}
    )
    assert not _is_verified_for_merge(
        {"status": "error", "is_verified": 0, "verification_json": None, "resolved_sha": None}
    )
