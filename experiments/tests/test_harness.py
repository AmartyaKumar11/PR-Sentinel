"""Offline harness tests. No network, Discord, or live agents."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from experiments.core import (
    MODEL_NOTE,
    PINNED_MODEL,
    CapError,
    ModelPinError,
    Slot,
    all_slots,
    append_run,
    branch_for,
    load_rows,
    mean,
    model_env,
    parse_numstat,
    pin_model,
    sample_sd,
    scrub,
)
from experiments.engine import InfraError, _account, run_slots
from experiments.fake import FakeEngine
from experiments.report import summarize, write_figures, write_summary
from experiments.tasks import TASKS


def test_prompts_stay_under_600():
    assert all(len(text) < 600 for text in TASKS.values())
    assert PINNED_MODEL == "claude-sonnet-5-thinking-high"
    env = model_env()
    assert env["cursor"] == PINNED_MODEL
    assert env["treefleet"] == PINNED_MODEL
    assert "5.5" in env["note"]
    assert env["note"] == MODEL_NOTE


def test_schedule_is_fifteen_round_robin():
    slots = all_slots()
    assert len(slots) == 15
    assert [(slot.task, slot.backend) for slot in slots[:5]] == [
        ("A", "cursor"),
        ("A", "treefleet"),
        ("B", "cursor"),
        ("B", "treefleet"),
        ("B", "treefleet-noref"),
    ]
    assert branch_for("exec1", slots[0]) == "exp/exec1/A/cursor/1"
    with pytest.raises(ValueError):
        branch_for("exec1", Slot("A", "cursor;rm", 1))


def test_mean_and_sample_sd():
    values = [10.0, 12.0, 14.0]
    assert mean(values) == 12
    assert sample_sd(values) == 2


def test_numstat_parsing():
    text = "1\t2\ta.py\n-\t-\tbin.dat\n3\t0\tb.py\n"
    assert parse_numstat(text) == (3, 4, 2)


@pytest.mark.parametrize(
    "bad",
    [
        "composer-2.5",
        "cursor-grok-4.5-high",
        "router",
        "auto",
        "claude-sonnet-5-thinking-xhigh",
        "claude-opus-5-thinking-high",
        "claude-fable-5-thinking-high",
        "gpt-5.6-sol-high",
        "claude-sonnet-5-5-medium",
    ],
)
def test_model_pin_refuses_every_other_id(bad):
    with pytest.raises(ModelPinError) as caught:
        pin_model([bad])
    assert caught.value.offered == [bad]
    assert PINNED_MODEL in str(caught.value)
    assert pin_model([PINNED_MODEL, bad]) == PINNED_MODEL


@pytest.mark.asyncio
async def test_resume_skips_finished_slots(tmp_path: Path):
    jsonl = tmp_path / "runs.jsonl"
    append_run(jsonl, {"slot": "A/cursor/1", "finished": True, "outcome": "success"}, [])
    engine = FakeEngine()
    await run_slots(all_slots()[:2], engine, jsonl, [])
    assert "seed:A/cursor/1" not in engine.calls
    assert "launch:A/treefleet/1" in engine.calls
    assert [row["slot"] for row in load_rows(jsonl)] == ["A/cursor/1", "A/treefleet/1"]


@pytest.mark.asyncio
async def test_cap_and_single_retry(tmp_path: Path):
    jsonl = tmp_path / "runs.jsonl"
    slots = [Slot("A", "cursor", rep) for rep in range(1, 17)]
    with pytest.raises(CapError):
        await run_slots(slots, FakeEngine(), jsonl, [])
    assert len(load_rows(jsonl)) == 15

    class Down(FakeEngine):
        def __init__(self):
            super().__init__()
            self.launches = 0

        async def launch(self, slot, seeded):
            self.launches += 1
            raise InfraError("transport down")

    retry_log = tmp_path / "retry.jsonl"
    down = Down()
    await run_slots([Slot("A", "cursor", 1), Slot("A", "treefleet", 1), Slot("B", "cursor", 1)], down, retry_log, [])
    rows = load_rows(retry_log)
    assert [row["slot"] for row in rows] == ["A/cursor/1", "A/treefleet/1"]
    assert rows[0]["retries"] == 1
    assert rows[0]["infra"] is True
    assert down.launches == 4


@pytest.mark.asyncio
async def test_bookkeeping_lock_keeps_every_streak_and_row(tmp_path: Path):
    state = {"streak": 0}
    stop = {"flag": False}
    lock = asyncio.Lock()
    engine = FakeEngine()
    jsonl = tmp_path / "runs.jsonl"
    records = [
        {"slot": "A/cursor/1", "finished": True, "outcome": "error", "infra": True, "time_to_result_s": None},
        {"slot": "A/cursor/2", "finished": True, "outcome": "error", "infra": True, "time_to_result_s": None},
    ]
    await asyncio.gather(*(_account(lock, state, stop, engine, jsonl, [], record) for record in records))
    assert state["streak"] == 2
    assert stop["flag"] is True
    assert [row["slot"] for row in load_rows(jsonl)] == ["A/cursor/1", "A/cursor/2"] or sorted(
        row["slot"] for row in load_rows(jsonl)
    ) == ["A/cursor/1", "A/cursor/2"]


@pytest.mark.asyncio
async def test_parallel_runs_record_each_slot_once(tmp_path: Path):
    class Slow(FakeEngine):
        async def poll(self, slot, agent_id):
            await asyncio.sleep(0.02)
            return await super().poll(slot, agent_id)

    slots = [Slot("A", "cursor", rep) for rep in range(1, 5)]
    jsonl = tmp_path / "parallel.jsonl"
    await run_slots(slots, Slow(), jsonl, [], parallel=True)
    rows = load_rows(jsonl)
    assert sorted(row["slot"] for row in rows) == [slot.key for slot in slots]
    assert {row["model"] for row in rows} == {PINNED_MODEL}


def test_secrets_never_land_in_records_or_logs(tmp_path: Path, caplog):
    secret = "super-secret-token-value"
    path = tmp_path / "runs.jsonl"
    append_run(path, {"slot": "A/cursor/1", "finished": True, "detail": f"boom {secret}"}, [secret])
    assert secret not in path.read_text(encoding="utf-8")
    with caplog.at_level(logging.INFO):
        logging.getLogger("experiments.test").info("%s", scrub(f"auth {secret}", [secret]))
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_dry_run_writes_summary_and_figures(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("experiments.report.ROOT", tmp_path)
    jsonl = tmp_path / "experiments" / "results" / "dryrun" / "runs.jsonl"
    await run_slots(all_slots(), FakeEngine(), jsonl, [])
    from experiments.report import write_report

    dest = write_report("dryrun", root=tmp_path)
    text = (dest / "summary.md").read_text(encoding="utf-8")
    assert "A/cursor" in text
    assert "3/3" in text
    assert (dest / "fig_time.pdf").is_file()
    assert (dest / "fig_success.pdf").is_file()
    table = summarize(load_rows(jsonl))
    cursor = next(row for row in table if row["condition"] == "A/cursor")
    assert cursor["time_mean"] == pytest.approx(32)
    assert cursor["time_sd"] == pytest.approx(1)
    write_summary(table, dest)
    write_figures(table, dest)
