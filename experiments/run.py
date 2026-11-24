"""python -m experiments.run --expid <id> [--dry-run] [--only task/backend]"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.core import (  # noqa: E402
    PINNED_MODEL,
    all_slots,
    filter_slots,
    model_env,
    smoke_ok,
    valid_expid,
)
from experiments.engine import run_slots  # noqa: E402
from experiments.fake import FakeEngine  # noqa: E402
from experiments.report import write_report  # noqa: E402


def _git_line(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _write_env(dest: Path, payload: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "env.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    if not valid_expid(args.expid):
        print("bad expid")
        return 2
    dest = ROOT / "experiments" / "results" / args.expid
    jsonl = dest / "runs.jsonl"
    slots = filter_slots(all_slots(), args.only or "")
    if args.only and not slots:
        print("no slots match --only")
        return 2
    secrets: list[str] = []
    if args.dry_run:
        engine = FakeEngine()
    else:
        from experiments.live import LiveEngine, apply_token_file, collect_secrets, resolve_models

        apply_token_file()
        secrets = collect_secrets()
        resolved = await resolve_models(secrets)
        if resolved != PINNED_MODEL:
            return 2
        engine = LiveEngine(args.expid, resolved, secrets)
    env = {
        "expid": args.expid,
        "dry_run": bool(args.dry_run),
        "os": platform.platform(),
        "python": platform.python_version(),
        "git": _git_line(["git", "--version"]),
        "seed_sha": _git_line(["git", "-C", r"D:\pr-sentinel-demo", "rev-parse", "main"]),
        "treefleet_sha": _git_line(["git", "-C", r"D:\TreeFleet", "rev-parse", "HEAD"]),
        "models": model_env(),
    }
    _write_env(dest, env)
    smoke = [
        slot
        for slot in slots
        if slot.task == "A" and slot.rep == 1 and slot.backend in {"cursor", "treefleet"}
    ]
    if not args.dry_run and not args.only and smoke:
        await run_slots(smoke, engine, jsonl, secrets, parallel=False)
        if not smoke_ok(_rows(jsonl)):
            print("smoke check failed; not starting the remaining slots")
            write_report(args.expid)
            return 2
        rest = [slot for slot in slots if slot not in smoke]
        await run_slots(rest, engine, jsonl, secrets, parallel=True)
    else:
        await run_slots(slots, engine, jsonl, secrets, parallel=not args.dry_run and not args.only)
    write_report(args.expid)
    return 0


def _rows(path: Path) -> list[dict]:
    from experiments.core import load_rows

    return load_rows(path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expid", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
