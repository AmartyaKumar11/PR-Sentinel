"""Run slots with a one-retry rule, a 15-run cap, and a two-error stop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from experiments.core import MAX_RUNS, CapError, Slot, append_run, load_done

TREEFLEET_BACKENDS = {"treefleet", "treefleet-noref"}


class InfraError(Exception):
    """HTTP or transport failure before a run id exists, or a later transport break."""


class LaunchFailed(Exception):
    """The backend answered, and the answer was a refusal. Not a retry."""


def _times(slot: Slot, status: dict) -> tuple[float | None, float | None]:
    if slot.backend == "cursor":
        if status.get("status") == "completed":
            return status.get("finished_s"), None
        return None, None
    finished = status.get("finished_s") if status.get("status") == "completed" else None
    return status.get("pr_seen_s"), finished


def _outcome(status: dict, metrics: dict) -> str:
    state = status.get("status")
    if state == "timeout":
        return "timeout"
    if state == "completed" and metrics.get("result_commit"):
        return "success"
    if state in {"failed", "cancelled", "expired", "completed"}:
        return "fail"
    return "error"


def _base_record(slot: Slot, *, retries: int, elapsed: float, infra: bool) -> dict:
    return {
        "slot": slot.key,
        "task": slot.task,
        "backend": slot.backend,
        "rep": slot.rep,
        "finished": True,
        "retries": retries,
        "infra": infra,
        "elapsed_s": round(elapsed, 3),
        "acceptance_pass": None,
        "time_to_result_s": None,
        "treefleet_finished_s": None,
        "files_changed": None,
        "lines_added": None,
        "lines_removed": None,
        "mergeable_into_pr_head": None,
        "result_commit": None,
        "pr_url": None,
        "token_usage": None,
        "model": None,
        "agent_id": None,
        "seed_head": None,
    }


async def run_one(slot: Slot, engine, secrets: list[str]) -> dict:
    retries = 0
    started = time.monotonic()
    seeded: dict = {}
    while True:
        try:
            seeded = await engine.seed(slot)
            launched = await engine.launch(slot, seeded)
            if not launched.get("agent_id"):
                raise InfraError("launch returned no run id")
            break
        except LaunchFailed as exc:
            record = _base_record(slot, retries=retries, elapsed=time.monotonic() - started, infra=False)
            record["outcome"] = "error"
            record["detail"] = str(exc)[:300]
            record["model"] = engine.model_for(slot)
            record["seed_head"] = (seeded or {}).get("seed_head")
            record["pr_url"] = (seeded or {}).get("pr_url")
            return record
        except InfraError as exc:
            if retries >= 1:
                record = _base_record(
                    slot, retries=retries, elapsed=time.monotonic() - started, infra=True
                )
                record["outcome"] = "error"
                record["detail"] = str(exc)[:300]
                record["model"] = engine.model_for(slot)
                return record
            retries += 1
            engine.note(f"retry {slot.key}")
    try:
        status = await asyncio.wait_for(engine.poll(slot, launched["agent_id"]), engine.timeout_s)
    except TimeoutError:
        status = dict(getattr(engine, "last", {}).get(launched["agent_id"]) or {})
        status["status"] = "timeout"
    except InfraError as exc:
        record = _base_record(slot, retries=retries, elapsed=time.monotonic() - started, infra=True)
        record["outcome"] = "error"
        record["detail"] = str(exc)[:300]
        record["agent_id"] = launched.get("agent_id")
        record["model"] = engine.model_for(slot)
        record["seed_head"] = seeded.get("seed_head")
        record["pr_url"] = seeded.get("pr_url")
        return record
    try:
        metrics = await engine.measure(slot, seeded, status)
    except Exception as exc:
        metrics = {"detail": type(exc).__name__}
    try:
        acceptance = await engine.accept(slot, metrics)
    except Exception:
        acceptance = None
    time_to, fleet_done = _times(slot, status)
    record = _base_record(slot, retries=retries, elapsed=time.monotonic() - started, infra=False)
    record.update(
        {
            "outcome": _outcome(status, metrics),
            "acceptance_pass": acceptance,
            "time_to_result_s": time_to,
            "treefleet_finished_s": fleet_done,
            "files_changed": metrics.get("files_changed"),
            "lines_added": metrics.get("lines_added"),
            "lines_removed": metrics.get("lines_removed"),
            "mergeable_into_pr_head": metrics.get("mergeable_into_pr_head"),
            "result_commit": metrics.get("result_commit"),
            "pr_url": metrics.get("pr_url") or seeded.get("pr_url"),
            "token_usage": status.get("token_usage"),
            "model": engine.model_for(slot),
            "agent_id": launched.get("agent_id"),
            "seed_head": seeded.get("seed_head"),
        }
    )
    return record


def _fmt_time(record: dict) -> str:
    value = record.get("time_to_result_s")
    if value is None:
        return "na"
    return f"{float(value):.0f}s"


async def _account(lock, state, stop, engine, jsonl, secrets, record) -> None:
    """Serialize the jsonl append and the infra streak. The yield is inside the lock."""
    async with lock:
        append_run(jsonl, record, secrets)
        engine.note(f"{record['slot']} {record['outcome']} {_fmt_time(record)}")
        prior = state["streak"]
        await asyncio.sleep(0)
        if record["outcome"] == "error" and record.get("infra"):
            state["streak"] = prior + 1
        else:
            state["streak"] = 0
        if state["streak"] >= 2:
            stop["flag"] = True
            engine.note("stopping after two consecutive infrastructure errors")


async def _finish_slot(slot, engine, jsonl, secrets, state, done, limit, stop, lock) -> None:
    record = await run_one(slot, engine, secrets)
    await _account(lock, state, stop, engine, jsonl, secrets, record)


async def run_slots(
    slots: list[Slot],
    engine,
    jsonl: Path,
    secrets: list[str],
    *,
    parallel: bool = False,
    limit: int = MAX_RUNS,
) -> None:
    done = load_done(jsonl)
    pending = [slot for slot in slots if slot.key not in done]
    state = {"started": 0, "streak": 0}
    stop = {"flag": False}
    lock = asyncio.Lock()
    if not parallel:
        for slot in pending:
            if stop["flag"]:
                break
            if len(done) + state["started"] >= limit:
                raise CapError(limit)
            state["started"] += 1
            await _finish_slot(slot, engine, jsonl, secrets, state, done, limit, stop, lock)
        return

    sem = {"cursor": asyncio.Semaphore(2), "treefleet": asyncio.Semaphore(1)}
    tasks: list[asyncio.Task] = []

    async def wrapped(slot: Slot, pool: str) -> None:
        try:
            if not stop["flag"]:
                await _finish_slot(slot, engine, jsonl, secrets, state, done, limit, stop, lock)
        finally:
            sem[pool].release()

    try:
        for slot in pending:
            pool = slot.pool
            await sem[pool].acquire()
            if stop["flag"] or len(done) + state["started"] >= limit:
                sem[pool].release()
                if not stop["flag"] and len(done) + state["started"] >= limit:
                    raise CapError(limit)
                break
            state["started"] += 1
            tasks.append(asyncio.create_task(wrapped(slot, pool)))
    finally:
        if tasks:
            await asyncio.gather(*tasks)
