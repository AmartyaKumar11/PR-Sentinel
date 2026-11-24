"""Scripted backends for --dry-run and offline tests. No network."""

from __future__ import annotations

from experiments.core import TREEFLEET_MODEL, Slot
from experiments.engine import InfraError


class FakeEngine:
    timeout_s = 30.0
    poll_s = 0.0

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cursor_model = TREEFLEET_MODEL
        self.secrets: list[str] = []
        self.fail_launches = 0
        self.last: dict = {}

    def note(self, text: str) -> None:
        print(text, flush=True)

    def model_for(self, slot: Slot) -> str:
        if slot.backend == "cursor":
            return self.cursor_model
        return TREEFLEET_MODEL

    async def seed(self, slot: Slot) -> dict:
        self.calls.append(f"seed:{slot.key}")
        return {
            "pr_number": 200 + slot.rep,
            "pr_url": f"https://github.com/AmartyaKumar11/pr-sentinel-demo/pull/{200 + slot.rep}",
            "branch": f"exp/dry/{slot.task}/{slot.backend}/{slot.rep}",
            "seed_head": "abc123def",
        }

    async def launch(self, slot: Slot, seeded: dict) -> dict:
        self.calls.append(f"launch:{slot.key}")
        if self.fail_launches:
            self.fail_launches -= 1
            raise InfraError("transport down")
        return {"agent_id": f"fake-{slot.key}"}

    async def poll(self, slot: Slot, agent_id: str) -> dict:
        base = {
            "A/cursor": 30,
            "A/treefleet": 40,
            "B/cursor": 35,
            "B/treefleet": 50,
            "B/treefleet-noref": 45,
        }[f"{slot.task}/{slot.backend}"]
        failed = slot.backend == "treefleet-noref"
        status = {
            "status": "failed" if failed else "completed",
            "pr_url": None if failed else f"https://github.com/o/r/pull/{300 + slot.rep}",
            "token_usage": None,
            "pr_seen_s": None if failed else float(base + slot.rep),
            "finished_s": float(base + slot.rep),
        }
        self.last[agent_id] = status
        return status

    async def measure(self, slot: Slot, seeded: dict, status: dict) -> dict:
        failed = slot.backend == "treefleet-noref"
        mergeable = not (slot.task == "B" and slot.backend == "cursor" and slot.rep == 3)
        if failed:
            mergeable = False
        return {
            "result_commit": "def456abc",
            "files_changed": 1 + slot.rep,
            "lines_added": 10 + slot.rep,
            "lines_removed": slot.rep,
            "mergeable_into_pr_head": mergeable,
            "pr_url": status.get("pr_url") or seeded.get("pr_url"),
        }

    async def accept(self, slot: Slot, metrics: dict) -> bool:
        return slot.backend != "treefleet-noref" and bool(metrics.get("result_commit"))
