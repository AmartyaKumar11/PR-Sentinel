"""Pure experiment rules: schedule, stats, model pin, records. No network."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

PINNED_MODEL = "claude-sonnet-5-thinking-high"
TREEFLEET_MODEL = PINNED_MODEL
MODEL_NOTE = (
    "Cursor's Cloud Agent API does not expose a Claude Sonnet 5.5 id. "
    "Both backends are pinned to claude-sonnet-5-thinking-high, the id both APIs expose."
)
MAX_RUNS = 15
_EXPID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_BRANCH = re.compile(
    r"^exp/[A-Za-z0-9][A-Za-z0-9._-]{0,31}/[AB]/(cursor|treefleet|treefleet-noref)/[1-9][0-9]*$"
)
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
CONDITIONS = (
    ("A", "cursor"),
    ("A", "treefleet"),
    ("B", "cursor"),
    ("B", "treefleet"),
    ("B", "treefleet-noref"),
)


class ModelPinError(Exception):
    def __init__(self, offered: list[str]):
        self.offered = list(offered)
        shown = ", ".join(self.offered) or "(none)"
        super().__init__(f"refusing every id other than {PINNED_MODEL}: {shown}")


class CapError(Exception):
    def __init__(self, limit: int = MAX_RUNS):
        super().__init__(f"refusing to start more than {limit} live runs")


@dataclass(frozen=True)
class Slot:
    task: str
    backend: str
    rep: int

    @property
    def key(self) -> str:
        return f"{self.task}/{self.backend}/{self.rep}"

    @property
    def pool(self) -> str:
        return "cursor" if self.backend == "cursor" else "treefleet"


def all_slots() -> list[Slot]:
    return [Slot(task, backend, rep) for rep in (1, 2, 3) for task, backend in CONDITIONS]


def filter_slots(slots: list[Slot], only: str) -> list[Slot]:
    if not only:
        return list(slots)
    task, backend = only.split("/", 1)
    return [slot for slot in slots if slot.task == task and slot.backend == backend]


def valid_expid(expid: str) -> bool:
    return bool(_EXPID.fullmatch(expid or ""))


def branch_for(expid: str, slot: Slot) -> str:
    branch = f"exp/{expid}/{slot.task}/{slot.backend}/{slot.rep}"
    if not valid_expid(expid) or not _BRANCH.fullmatch(branch):
        raise ValueError(f"unsafe branch name for {slot.key}")
    return branch


def valid_sha(value: str) -> bool:
    return bool(_SHA.fullmatch(value or ""))


def model_env() -> dict[str, str]:
    return {"cursor": PINNED_MODEL, "treefleet": PINNED_MODEL, "note": MODEL_NOTE}


def pin_model(ids: list[str]) -> str:
    """Accept only the pinned id. Every other id is a refusal."""
    offered = [str(item) for item in ids]
    if PINNED_MODEL not in offered:
        raise ModelPinError(offered)
    return PINNED_MODEL


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def sample_sd(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    center = mean(values)
    assert center is not None
    return math.sqrt(sum((item - center) ** 2 for item in values) / (n - 1))


def parse_numstat(text: str) -> tuple[int, int, int]:
    files = added = removed = 0
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return files, added, removed


def scrub(text: str, secrets: list[str]) -> str:
    clean = text or ""
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, "[redacted]")
    return clean


def scrub_obj(value, secrets: list[str]):
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, dict):
        return {str(key): scrub_obj(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_obj(item, secrets) for item in value]
    return value


def prepare_record(record: dict, secrets: list[str]) -> dict:
    clean = scrub_obj(record, secrets)
    blob = json.dumps(clean, sort_keys=True)
    for secret in secrets:
        if secret and secret in blob:
            raise RuntimeError("refusing to write a record that contains a secret")
    return clean


def append_run(path: Path, record: dict, secrets: list[str]) -> None:
    clean = prepare_record(record, secrets)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(clean, sort_keys=True) + "\n")


def load_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_done(path: Path) -> set[str]:
    return {row["slot"] for row in load_rows(path) if row.get("finished") and row.get("slot")}


def smoke_ok(rows: list[dict]) -> bool:
    by = {row.get("slot"): row for row in rows}
    for key in ("A/cursor/1", "A/treefleet/1"):
        row = by.get(key)
        if not row or not row.get("result_commit"):
            return False
        if row.get("acceptance_pass") not in (True, False):
            return False
    return True
