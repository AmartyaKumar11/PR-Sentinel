"""Per-repo context. One indexed read on the review path; writes happen after."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from app.database import get_db
from app.services.context_sampler import sample_files

logger = logging.getLogger(__name__)

DECAY = 0.95
HARD_FIELDS = (
    "language",
    "package_manager",
    "test_framework",
    "test_location",
    "linter",
    "ci_checks",
    "type_checker",
)
CONVENTION_FIELDS = ("import_style", "file_naming", "error_handling", "test_style")
FILE_CAP = 500
INSTRUCTION_FILES = (
    ".coderabbit.yaml",
    ".coderabbit.yml",
    "AGENTS.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    "CONTRIBUTING.md",
)
_PROSE_EXTRACT = """Extract only what this repository document explicitly states. Return JSON with these keys: test_framework, import_style, file_naming, error_handling, test_style, rules. Use an empty string when the document does not state it. import_style is one of absolute, relative, alias. rules is one short sentence of hard rules."""
CONFIG_PATHS = (
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lock",
    "pyproject.toml",
    "requirements.txt",
    "pytest.ini",
    "setup.cfg",
    "go.mod",
    "tsconfig.json",
    "mypy.ini",
    "pyrightconfig.json",
    "ruff.toml",
    ".ruff.toml",
    ".eslintrc",
    ".eslintrc.json",
    "eslint.config.js",
    "eslint.config.mjs",
)
_FACT_FILES = {
    "language": {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "tsconfig.json", "__tree__"},
    "package_manager": {
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "bun.lock",
        "go.mod",
        "pyproject.toml",
        "requirements.txt",
    },
    "test_framework": {"package.json", "pyproject.toml", "requirements.txt", "pytest.ini", "setup.cfg", "__tree__"},
    "test_location": {"__tree__", "pytest.ini"},
    "linter": {
        "package.json",
        "pyproject.toml",
        ".eslintrc",
        ".eslintrc.json",
        "eslint.config.js",
        "eslint.config.mjs",
        "ruff.toml",
        ".ruff.toml",
    },
    "type_checker": {"tsconfig.json", "mypy.ini", "pyproject.toml", "pyrightconfig.json"},
}
_RUNNERS = ("vitest", "jest", "mocha", "pytest", "unittest")
_refresh_tasks: set[asyncio.Task] = set()
_index_inflight: set[str] = set()
_write_locks: dict[str, asyncio.Lock] = {}


def _lock(repo: str) -> asyncio.Lock:
    return _write_locks.setdefault(repo, asyncio.Lock())


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fact(value: str, source: str = "config") -> dict:
    return {
        "value": value,
        "confidence": 1.0 if source == "config" else 0.95,
        "source": source,
        "overridden_by_user": False,
    }


def extract_hard_facts(files: dict[str, str], paths: list[str] | None = None) -> dict:
    """Authoritative facts from config text and the file tree. No conventions."""
    paths = paths if paths is not None else [path for path in files if _is_source(path)]
    facts: dict[str, dict] = {}
    package = _json_file(files.get("package.json"))
    deps = {}
    if package:
        deps = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    language = _language(files, paths, deps)
    if language:
        facts["language"] = _fact(language)
    manager = _package_manager(files)
    if manager:
        facts["package_manager"] = _fact(manager)
    framework = _test_framework(files, paths, deps)
    if framework:
        facts["test_framework"] = _fact(framework)
    location = _test_location(paths)
    if location:
        facts["test_location"] = _fact(location)
    linter = _linter(files, deps)
    if linter:
        facts["linter"] = _fact(linter)
    checks = _ci_checks(files)
    if checks:
        facts["ci_checks"] = _fact(checks)
    checker = _type_checker(files, deps)
    if checker:
        facts["type_checker"] = _fact(checker)
    return facts


def config_hashes(files: dict[str, str], paths: list[str]) -> dict[str, str]:
    hashes = {}
    for path, text in files.items():
        if _is_config(path):
            hashes[path] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    hashes["__tree__"] = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    return hashes


def merge_hard_facts(old: dict, old_hashes: dict, files: dict[str, str], paths: list[str], now: str) -> tuple[dict, dict]:
    fresh = extract_hard_facts(files, paths)
    new_hashes = config_hashes(files, paths)
    if not old_hashes:
        return _stamp(fresh, now), new_hashes
    changed = {path for path in set(old_hashes) | set(new_hashes) if old_hashes.get(path) != new_hashes.get(path)}
    touched: set[str] = set()
    for path in changed:
        touched |= _affects(path)
    out = {}
    for key in set(old) | set(fresh):
        prev = old.get(key)
        nxt = fresh.get(key)
        if prev and prev.get("overridden_by_user"):
            if nxt and nxt["value"] != prev["value"]:
                out[key] = {**nxt, "last_seen": now, "overridden_by_user": False}
            else:
                out[key] = prev
            continue
        if prev and prev.get("contradicted"):
            if nxt:
                out[key] = {**nxt, "last_seen": now}
            else:
                out[key] = prev
            continue
        if key in touched:
            if nxt:
                out[key] = {**nxt, "last_seen": now}
            continue
        if prev:
            out[key] = prev
        elif nxt:
            out[key] = {**nxt, "last_seen": now}
    return out, new_hashes


def reinforce(existing: dict, samples: dict[str, dict[str, int]], now: str) -> dict:
    """Decay, add samples, recompute. A user override keeps its dominant."""
    out = {}
    for key in set(existing) | set(samples):
        prev = existing.get(key) or {}
        counts = {name: float(count) * DECAY for name, count in (prev.get("counts") or {}).items()}
        evidence = float(prev.get("evidence_count") or 0) * DECAY
        for name, count in (samples.get(key) or {}).items():
            if count:
                counts[name] = counts.get(name, 0.0) + count
                evidence += count
        counts = {name: count for name, count in counts.items() if count > 1e-6}
        if not counts or evidence <= 0:
            continue
        frozen = bool(prev.get("overridden_by_user"))
        pinned = bool(prev.get("pinned_by_instruction")) and not frozen
        if frozen:
            dominant, confidence = prev.get("dominant"), 1.0
        elif pinned:
            dominant, confidence = prev.get("dominant"), 0.98
        else:
            dominant = max(counts, key=counts.get)
            confidence = counts.get(dominant, 0) / evidence
        out[key] = {
            "counts": counts,
            "evidence_count": evidence,
            "dominant": dominant,
            "confidence": round(confidence, 4),
            "last_seen": now,
            "overridden_by_user": frozen,
            "pinned_by_instruction": pinned,
            "origin": prev.get("origin") or "",
        }
    return out


def render_context(row: dict | None) -> str:
    """Confidence-scaled hint text. Empty when there is nothing firm enough to say."""
    if not row:
        return ""
    lines = []
    for key, fact in (row.get("hard_facts") or {}).items():
        if str(key).startswith("_") or not isinstance(fact, dict):
            continue
        if fact.get("contradicted") or float(fact.get("confidence") or 0) < 0.95:
            continue
        value = fact.get("value")
        if value:
            lines.append(_fact_line(key, value, fact))
    for key, conv in (row.get("conventions") or {}).items():
        if str(key).startswith("_") or not isinstance(conv, dict):
            continue
        line = _convention_line(key, conv)
        if line:
            lines.append(line)
    rules = (row.get("instruction_text") or "").strip()
    if rules:
        lines.append("Repo rules:\n" + rules[:1500])
    if not lines:
        return ""
    lines.append("If the code disagrees with any hint above, follow the code.")
    return "\n".join(lines)


def with_context(base: str, block: str) -> str:
    if not block:
        return base
    return base + "\n\n" + block


def attach_context(prompt: str, block: str) -> str:
    if not block:
        return prompt
    return prompt.rstrip() + "\n\n## Repo context\n" + block


def format_context(row: dict | None) -> str:
    if not row or (not row.get("hard_facts") and not row.get("conventions")):
        return "No context for this repo yet. It is filled in after the first review.\nCorrect anything wrong with /context set <field> <value>."
    lines = [f"**Context for {row.get('repo_full_name', 'this repo')}**", "", "**Hard facts**"]
    facts = row.get("hard_facts") or {}
    if not facts:
        lines.append("• none")
    for key, fact in facts.items():
        if str(key).startswith("_") or not isinstance(fact, dict):
            continue
        flag = " override" if fact.get("overridden_by_user") else ""
        lines.append(
            f"• {key}: {fact.get('value')} ({float(fact.get('confidence') or 0):.0%}, {fact.get('source')}{flag})"
        )
    lines.append("")
    lines.append("**Conventions**")
    conv = row.get("conventions") or {}
    if not conv:
        lines.append("• none yet")
    for key, item in conv.items():
        if str(key).startswith("_") or not isinstance(item, dict):
            continue
        flag = " override" if item.get("overridden_by_user") else ""
        lines.append(f"• {key}: {item.get('dominant')} ({float(item.get('confidence') or 0):.0%}{flag})")
    lines.append("")
    lines.append("Correct anything wrong with /context set <field> <value>.")
    return "\n".join(lines)


def contradicted_facts(facts: dict, gate_result: dict, diff: str) -> list[str]:
    """Facts the agent followed that CI showed were the wrong framework."""
    ci = json.dumps(gate_result or {}, default=str).lower()
    diff_text = (diff or "").lower()
    hit = []
    fact = (facts or {}).get("test_framework") or {}
    value = str(fact.get("value") or "").lower()
    if not value or float(fact.get("confidence") or 0) <= 0:
        return hit
    if not re.search(rf"\b{re.escape(value)}\b", diff_text):
        return hit
    for runner in _RUNNERS:
        if runner != value and re.search(rf"\b{re.escape(runner)}\b", ci):
            hit.append("test_framework")
            break
    return hit


def collapse_fact(facts: dict, key: str, now: str | None = None) -> None:
    fact = facts.get(key)
    if not fact:
        return
    fact["confidence"] = 0
    fact["contradicted"] = True
    fact["last_seen"] = now or _now()


async def load_context(db, repo: str) -> dict | None:
    """Single primary-key read."""
    cursor = await db.execute(
        "SELECT repo_full_name, hard_facts, conventions, config_hashes, created_at, updated_at, version, "
        "full_index_done, instruction_text "
        "FROM repo_context WHERE repo_full_name = ?",
        (repo,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    for key in ("hard_facts", "conventions", "config_hashes"):
        try:
            data[key] = json.loads(data[key] or "{}")
        except json.JSONDecodeError:
            data[key] = {}
    return data


async def save_context(db, row: dict) -> None:
    await db.execute(
        """INSERT INTO repo_context
           (repo_full_name, hard_facts, conventions, config_hashes, created_at, updated_at, version,
            full_index_done, instruction_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(repo_full_name) DO UPDATE SET
             hard_facts = excluded.hard_facts,
             conventions = excluded.conventions,
             config_hashes = excluded.config_hashes,
             updated_at = excluded.updated_at,
             version = excluded.version,
             full_index_done = excluded.full_index_done,
             instruction_text = excluded.instruction_text""",
        (
            row["repo_full_name"],
            json.dumps(row.get("hard_facts") or {}),
            json.dumps(row.get("conventions") or {}),
            json.dumps(row.get("config_hashes") or {}),
            row["created_at"],
            row["updated_at"],
            row.get("version") or 1,
            1 if row.get("full_index_done") else 0,
            row.get("instruction_text") or "",
        ),
    )
    await db.commit()


def _blank(repo: str, now: str) -> dict:
    return {
        "repo_full_name": repo,
        "hard_facts": {},
        "conventions": {},
        "config_hashes": {},
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "full_index_done": 0,
        "instruction_text": "",
    }


def project_row(row: dict, files: dict[str, str], paths: list[str], sources: dict[str, str] | None) -> dict:
    """Pure update: hashes, facts, then decay and reinforce. sources=None skips conventions."""
    now = _now()
    facts, hashes = merge_hard_facts(
        row.get("hard_facts") or {},
        row.get("config_hashes") or {},
        files,
        paths,
        now,
    )
    conventions = row.get("conventions") or {}
    if sources is not None:
        conventions = reinforce(conventions, sample_files(sources), now)
    for path, digest in (row.get("config_hashes") or {}).items():
        if _is_instruction(path):
            hashes.setdefault(path, digest)
    return {
        **row,
        "hard_facts": facts,
        "conventions": conventions,
        "config_hashes": hashes,
        "updated_at": now,
    }


async def bootstrap_context(db, repo: str, files: dict[str, str], paths: list[str]) -> dict:
    """First review: hard facts only. Conventions stay empty unless the index already wrote them."""
    async with _lock(repo):
        current = await load_context(db, repo)
        row = project_row(current or _blank(repo, _now()), files, paths, None)
        await save_context(db, row)
        return row


async def apply_review(db, repo: str, files: dict[str, str], paths: list[str], sources: dict[str, str]) -> dict:
    async with _lock(repo):
        row = await load_context(db, repo) or _blank(repo, _now())
        updated = project_row(row, files, paths, sources)
        await save_context(db, updated)
        return updated


async def context_for_diagnosis(owner: str, repo: str, ref: str) -> str:
    """Indexed read. The first review of a repo also reads config files once."""
    full = f"{owner}/{repo}"
    db = await get_db()
    row = await load_context(db, full)
    if row is None or not row.get("full_index_done"):
        spawn_full_index(owner, repo, ref)
    if row is None:
        files, paths = await fetch_snapshot(owner, repo, ref)
        if not files and not paths:
            return ""
        row = await bootstrap_context(db, full, files, paths)
    return render_context(row)


def spawn_refresh(owner: str, repo: str, ref: str, changed_paths: list[str]) -> None:
    """Fire-and-forget. Never awaited by the review."""
    task = asyncio.create_task(_refresh(owner, repo, ref, list(changed_paths or [])))
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)


async def _refresh(owner: str, repo: str, ref: str, changed_paths: list[str]) -> None:
    try:
        files, paths = await fetch_snapshot(owner, repo, ref, changed_paths)
        if not files and not paths:
            return
        full = f"{owner}/{repo}"
        db = await get_db()
        current = await load_context(db, full)
        instructions = await read_instruction_files(files, (current or {}).get("config_hashes") or {})
        sources = {path: files[path] for path in changed_paths if path in files and _is_source(path)}
        await apply_review(db, full, files, paths, sources)
        if instructions:
            await _store_instructions(db, full, files, instructions)
    except Exception:
        logger.warning("context refresh failed", exc_info=True)


def spawn_full_index(owner: str, repo: str, ref: str) -> None:
    """Full-repo sample. Never awaited by a review."""
    full = f"{owner}/{repo}"
    if full in _index_inflight:
        return
    _index_inflight.add(full)
    task = asyncio.create_task(_run_full_index(owner, repo, ref))
    _refresh_tasks.add(task)

    def _done(done: asyncio.Task, key: str = full) -> None:
        _index_inflight.discard(key)
        _refresh_tasks.discard(done)

    task.add_done_callback(_done)


async def _run_full_index(owner: str, repo: str, ref: str) -> None:
    full = f"{owner}/{repo}"
    try:
        db = await get_db()
        existing = await load_context(db, full)
        if existing and existing.get("full_index_done"):
            return
        from app.services.github_client import GitHubClient

        github = GitHubClient()
        try:
            paths = await github.list_tree_paths(owner, repo, ref)
            chosen = select_source_files(paths)
            rules = await github.list_dir(owner, repo, ".cursor/rules", ref)
            wanted = list(dict.fromkeys([*chosen, *CONFIG_PATHS, *INSTRUCTION_FILES, *rules]))
            files = await _read_many(github, owner, repo, ref, wanted)
        finally:
            await github.close()
        sources = {path: files[path] for path in chosen if path in files}
        instructions = await read_instruction_files(files, (existing or {}).get("config_hashes") or {})
        async with _lock(full):
            row = await load_context(db, full) or _blank(full, _now())
            if row.get("full_index_done"):
                return
            updated = project_row(row, files, paths, None)
            updated["conventions"] = install_baseline(
                updated.get("conventions") or {},
                sample_files(sources),
                len(sources),
                _now(),
            )
            if instructions:
                updated["hard_facts"] = merge_instruction_facts(updated["hard_facts"], instructions["hard"])
                updated["conventions"] = pin_conventions(updated["conventions"], instructions["pins"], _now())
                updated["instruction_text"] = instructions["text"]
                if instructions.get("parsed"):
                    stamp_instruction_hashes(updated["config_hashes"], files)
            updated["full_index_done"] = 1
            await save_context(db, updated)
    except Exception:
        logger.warning("full repo index failed", exc_info=True)


async def _store_instructions(db, repo: str, files: dict[str, str], instructions: dict) -> None:
    async with _lock(repo):
        row = await load_context(db, repo)
        if not row:
            return
        row["hard_facts"] = merge_instruction_facts(row.get("hard_facts") or {}, instructions["hard"])
        row["conventions"] = pin_conventions(row.get("conventions") or {}, instructions["pins"], _now())
        row["instruction_text"] = instructions["text"]
        if instructions.get("parsed"):
            stamp_instruction_hashes(row["config_hashes"], files)
        row["updated_at"] = _now()
        await save_context(db, row)


async def _read_many(github, owner: str, repo: str, ref: str, paths: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for start in range(0, len(paths), 16):
        chunk = paths[start : start + 16]
        bodies = await asyncio.gather(*[github.read_text(owner, repo, path, ref) for path in chunk])
        for path, text in zip(chunk, bodies):
            if text is not None:
                files[path] = text
    return files


async def fetch_snapshot(
    owner: str, repo: str, ref: str, extra: list[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    from app.services.github_client import GitHubClient

    github = GitHubClient()
    try:
        workflows, rules, paths = await asyncio.gather(
            github.list_dir(owner, repo, ".github/workflows", ref),
            github.list_dir(owner, repo, ".cursor/rules", ref),
            _tree(github, owner, repo, ref),
        )
        wanted = list(dict.fromkeys([*CONFIG_PATHS, *INSTRUCTION_FILES, *workflows, *rules, *(extra or [])]))
        loaded = await asyncio.gather(
            *[github.read_text(owner, repo, path, ref) for path in wanted]
        )
        files = {path: text for path, text in zip(wanted, loaded) if text is not None}
        return files, paths
    finally:
        await github.close()


async def _tree(github, owner: str, repo: str, ref: str) -> list[str]:
    try:
        return await github.get_file_tree(owner, repo, ref)
    except Exception:
        logger.warning("context tree read failed", exc_info=True)
        return []


async def set_override(db, repo: str, field: str, value: str) -> dict:
    if field not in HARD_FIELDS and field not in CONVENTION_FIELDS:
        raise ValueError(f"Unknown context field: {field}")
    now = _now()
    row = await load_context(db, repo) or _blank(repo, now)
    if field in HARD_FIELDS:
        facts = dict(row.get("hard_facts") or {})
        facts[field] = {
            "value": value,
            "confidence": 1.0,
            "source": "user_override",
            "last_seen": now,
            "overridden_by_user": True,
        }
        row["hard_facts"] = facts
    else:
        conv = dict(row.get("conventions") or {})
        prev = conv.get(field) or {}
        conv[field] = {
            "counts": prev.get("counts") or {},
            "evidence_count": prev.get("evidence_count") or 0,
            "dominant": value,
            "confidence": 1.0,
            "last_seen": now,
            "overridden_by_user": True,
        }
        row["conventions"] = conv
    row["updated_at"] = now
    await save_context(db, row)
    return row


async def correct_failed_context(task_id: str, gate_result: dict, diff: str) -> str:
    """Collapse a context claim CI contradicted. Empty string when nothing changed."""
    from app.services.task_manager import get_task

    db = await get_db()
    task = await get_task(db, task_id)
    if not task:
        return ""
    row = await load_context(db, task["repo"])
    if not row:
        return ""
    keys = contradicted_facts(row.get("hard_facts") or {}, gate_result, diff)
    if not keys:
        return ""
    now = _now()
    for key in keys:
        collapse_fact(row["hard_facts"], key, now)
    row["updated_at"] = now
    await save_context(db, row)
    kept = render_context(row)
    return "Contradicted context was removed: " + ", ".join(keys) + (f"\n{kept}" if kept else "")


def _stamp(facts: dict, now: str) -> dict:
    return {key: {**fact, "last_seen": now} for key, fact in facts.items()}


def _affects(path: str) -> set[str]:
    if path.startswith(".github/workflows/"):
        return {"ci_checks"}
    hits = {fact for fact, paths in _FACT_FILES.items() if path in paths}
    return hits


def _is_config(path: str) -> bool:
    normal = path.replace("\\", "/")
    return normal in CONFIG_PATHS or normal.startswith(".github/workflows/")


def _is_source(path: str) -> bool:
    from app.services.graph_builder import is_supported_source

    return is_supported_source(path)


def _is_instruction(path: str) -> bool:
    normal = path.replace("\\", "/")
    if normal in INSTRUCTION_FILES:
        return True
    return normal.startswith(".cursor/rules/") and normal.endswith(".mdc")


def select_source_files(paths: list[str]) -> list[str]:
    """Supported source, hard-capped. Over the cap, draw across directories, largest first."""
    source = [path.replace("\\", "/") for path in paths if _is_source(path)]
    if len(source) <= FILE_CAP:
        return source
    buckets: dict[str, list[str]] = {}
    for path in source:
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        buckets.setdefault(directory, []).append(path)
    ordered = sorted(buckets, key=lambda name: (-len(buckets[name]), name))
    for name in ordered:
        buckets[name].sort()
    picked: list[str] = []
    while len(picked) < FILE_CAP and any(buckets[name] for name in ordered):
        for name in ordered:
            if not buckets[name]:
                continue
            picked.append(buckets[name].pop(0))
            if len(picked) >= FILE_CAP:
                break
    return picked


def seed_conventions(samples: dict, n_files: int, now: str) -> dict:
    """Initial counts from one full-repo walk. evidence_count is files sampled."""
    out = {}
    if n_files <= 0:
        return out
    for key, counts in samples.items():
        if not counts:
            continue
        dominant = max(counts, key=counts.get)
        confidence = min(1.0, float(counts[dominant]) / n_files)
        out[key] = {
            "counts": {name: float(count) for name, count in counts.items()},
            "evidence_count": n_files,
            "dominant": dominant,
            "confidence": round(confidence, 4),
            "last_seen": now,
            "overridden_by_user": False,
            "pinned_by_instruction": False,
            "origin": "",
        }
    return out


def install_baseline(existing: dict, samples: dict, n_files: int, now: str) -> dict:
    """Seed an empty row. If a diff already landed, add the baseline without decay."""
    seeded = seed_conventions(samples, n_files, now)
    if not existing:
        return seeded
    out = dict(existing)
    for key, item in seeded.items():
        prev = existing.get(key) or {}
        counts = {name: float(count) for name, count in (prev.get("counts") or {}).items()}
        for name, count in item["counts"].items():
            counts[name] = counts.get(name, 0.0) + float(count)
        evidence = float(prev.get("evidence_count") or 0) + n_files
        frozen = bool(prev.get("overridden_by_user"))
        pinned = bool(prev.get("pinned_by_instruction")) and not frozen
        if frozen:
            dominant, confidence = prev.get("dominant"), 1.0
        elif pinned:
            dominant, confidence = prev.get("dominant"), 0.98
        else:
            dominant = max(counts, key=counts.get)
            confidence = min(1.0, counts[dominant] / evidence) if evidence else 0.0
        out[key] = {
            "counts": counts,
            "evidence_count": evidence,
            "dominant": dominant,
            "confidence": round(float(confidence), 4),
            "last_seen": now,
            "overridden_by_user": frozen,
            "pinned_by_instruction": pinned,
            "origin": prev.get("origin") or "",
        }
    return out


def _instruction_fact(value: str, origin: str) -> dict:
    return {
        "value": value,
        "confidence": 0.98,
        "source": "instruction_file",
        "origin": origin,
        "overridden_by_user": False,
    }


def _scan_mentions(text: str, origin: str) -> tuple[dict, dict]:
    low = text.lower()
    hard: dict = {}
    pins: dict = {}
    for name in ("vitest", "jest", "mocha", "pytest", "unittest"):
        if re.search(rf"\b{name}\b", low):
            hard["test_framework"] = _instruction_fact(name, origin)
            break
    if "absolute import" in low:
        pins["import_style"] = ("absolute", origin)
    elif "relative import" in low:
        pins["import_style"] = ("relative", origin)
    elif "@/" in text or "path alias" in low:
        pins["import_style"] = ("alias", origin)
    if "snake_case" in low or "snake case" in low:
        pins["file_naming"] = ("snake", origin)
    elif "kebab-case" in low or "kebab case" in low:
        pins["file_naming"] = ("kebab", origin)
    elif "camelcase" in low or "camel case" in low:
        pins["file_naming"] = ("camel", origin)
    return hard, pins


def parse_coderabbit(text: str, origin: str = ".coderabbit.yaml") -> tuple[dict, dict]:
    """Map path instructions and review lines onto known context fields. No YAML dependency."""
    return _scan_mentions(text, origin)


def _norm_convention(key: str, value: str) -> str:
    low = value.strip().lower()
    if key == "import_style":
        if "alias" in low or "@/" in value:
            return "alias"
        if "relative" in low:
            return "relative"
        if "absolute" in low:
            return "absolute"
    if key == "file_naming":
        if "snake" in low:
            return "snake"
        if "kebab" in low:
            return "kebab"
        if "pascal" in low:
            return "pascal"
        if "camel" in low:
            return "camel"
    if key == "test_style":
        if "colocat" in low or "__tests__" in low:
            return "colocated"
        if "separate" in low or low.startswith("tests/"):
            return "separate"
    if key == "error_handling":
        if "throw" in low or "raise" in low:
            return "throw"
        if "return" in low:
            return "return_error"
    return value.strip()


def _loads_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def merge_instruction_facts(facts: dict, hard: dict) -> dict:
    """Instruction files fill gaps. Config and a user override keep the hard fact."""
    out = dict(facts)
    for key, fact in hard.items():
        prev = out.get(key) if isinstance(out.get(key), dict) else None
        if prev and (prev.get("overridden_by_user") or prev.get("source") == "config"):
            continue
        out[key] = {**fact, "last_seen": fact.get("last_seen") or _now()}
    return out


def pin_conventions(conventions: dict, pins: dict, now: str) -> dict:
    """A stated rule freezes the dominant. Later samples still count, they do not flip it."""
    out = dict(conventions)
    for key, spec in pins.items():
        dominant, origin = spec if isinstance(spec, tuple) else (spec, "")
        if not dominant:
            continue
        prev = out.get(key) or {}
        if prev.get("overridden_by_user"):
            continue
        out[key] = {
            "counts": prev.get("counts") or {},
            "evidence_count": prev.get("evidence_count") or 0,
            "dominant": dominant,
            "confidence": 0.98,
            "last_seen": now,
            "overridden_by_user": False,
            "pinned_by_instruction": True,
            "origin": origin,
        }
    return out


def _instruction_changed(files: dict[str, str], old_hashes: dict) -> bool:
    present = {path: text for path, text in files.items() if _is_instruction(path)}
    tracked = {path for path in old_hashes if path != "__tree__" and _is_instruction(path)}
    if not present and not tracked:
        return False
    for path, text in present.items():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != old_hashes.get(path):
            return True
    return any(path not in present for path in tracked)


def stamp_instruction_hashes(hashes: dict, files: dict[str, str]) -> None:
    """Record instruction-file hashes only after a parse, so an unread file is not treated as done."""
    for path in [path for path in hashes if _is_instruction(path)]:
        hashes.pop(path, None)
    for path, text in files.items():
        if _is_instruction(path):
            hashes[path] = hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _extract_prose(prose: list[tuple[str, str]], llm) -> tuple[dict, dict, str, bool]:
    """One model call for every changed prose file. Failure keeps the raw text and invents nothing."""
    origin = prose[0][0]
    body = "\n\n".join(f"FILE {path}\n{text[:4000]}" for path, text in prose)
    raw_text = "\n\n".join(f"{path}:\n{text.strip()}" for path, text in prose)[:1500]
    if llm is None:
        from app.services.llm_client import LLMClient

        llm = LLMClient()
    try:
        raw = await llm.chat(_PROSE_EXTRACT, [{"role": "user", "content": body}], thinking=False)
        data = _loads_json(raw)
    except Exception:
        logger.warning("instruction extract failed", exc_info=True)
        return {}, {}, raw_text, False
    hard: dict = {}
    pins: dict = {}
    framework = str(data.get("test_framework") or "").strip()
    if framework:
        hard["test_framework"] = _instruction_fact(framework.split()[0].lower(), origin)
    for key in CONVENTION_FIELDS:
        value = str(data.get(key) or "").strip()
        if value:
            pins[key] = (_norm_convention(key, value), origin)
    rules = str(data.get("rules") or "").strip()
    if rules:
        raw_text = (rules + "\n\n" + raw_text)[:1500]
    return hard, pins, raw_text, True


async def read_instruction_files(files: dict[str, str], old_hashes: dict, llm=None) -> dict | None:
    """Parse instruction files when their hash changed. None means unchanged."""
    if not _instruction_changed(files, old_hashes or {}):
        return None
    hard: dict = {}
    pins: dict = {}
    prose: list[tuple[str, str]] = []
    for path, text in files.items():
        if not _is_instruction(path):
            continue
        name = path.replace("\\", "/")
        if name.rsplit("/", 1)[-1].startswith(".coderabbit."):
            got_hard, got_pins = parse_coderabbit(text, name)
            hard.update(got_hard)
            pins.update(got_pins)
        else:
            prose.append((name, text))
    text_out = ""
    parsed = True
    if prose:
        got_hard, got_pins, text_out, parsed = await _extract_prose(prose, llm)
        for key, fact in got_hard.items():
            hard.setdefault(key, fact)
        for key, spec in got_pins.items():
            pins.setdefault(key, spec)
    return {"hard": hard, "pins": pins, "text": text_out, "parsed": parsed}


def _json_file(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _language(files: dict, paths: list[str], deps: dict) -> str | None:
    if "go.mod" in files:
        return "go"
    if "tsconfig.json" in files or "typescript" in deps:
        return "typescript"
    if "pyproject.toml" in files or "requirements.txt" in files:
        return "python"
    counts: dict[str, int] = {}
    for path in paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext:
            counts[ext] = counts.get(ext, 0) + 1
    if counts.get("py", 0) and counts.get("py", 0) >= counts.get("ts", 0):
        return "python"
    if counts.get("ts") or counts.get("tsx"):
        return "typescript"
    if counts.get("js") or counts.get("jsx"):
        return "javascript"
    if counts.get("go"):
        return "go"
    return None


def _package_manager(files: dict) -> str | None:
    if "pnpm-lock.yaml" in files:
        return "pnpm"
    if "bun.lock" in files:
        return "bun"
    if "yarn.lock" in files:
        return "yarn"
    if "package-lock.json" in files:
        return "npm"
    if "go.mod" in files:
        return "go"
    if "pyproject.toml" in files or "requirements.txt" in files:
        return "pip"
    return None


def _test_framework(files: dict, paths: list[str], deps: dict) -> str | None:
    for name in ("vitest", "jest", "mocha"):
        if name in deps:
            return name
    blob = "\n".join(files.get(name, "") for name in ("pyproject.toml", "requirements.txt", "pytest.ini", "setup.cfg"))
    if re.search(r"\bpytest\b", blob):
        return "pytest"
    if any(path.replace("\\", "/").rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py") for path in paths):
        return "pytest"
    if any(path.endswith("_test.go") for path in paths):
        return "go test"
    return None


def _test_location(paths: list[str]) -> str | None:
    colocated = separate = 0
    for path in paths:
        normal = path.replace("\\", "/")
        name = normal.rsplit("/", 1)[-1]
        if not (
            name.startswith("test_")
            or ".test." in name
            or ".spec." in name
            or name.endswith("_test.go")
            or "/__tests__/" in f"/{normal}"
        ):
            continue
        if normal.startswith("tests/") or "/tests/" in f"/{normal}":
            separate += 1
        else:
            colocated += 1
    if colocated == 0 and separate == 0:
        return None
    if colocated > separate:
        return "__tests__/ co-located"
    return "tests/"


def _linter(files: dict, deps: dict) -> str | None:
    found = []
    if any(name in files for name in (".eslintrc", ".eslintrc.json", "eslint.config.js", "eslint.config.mjs")) or "eslint" in deps:
        found.append("eslint")
    if "prettier" in deps:
        found.append("prettier")
    if any(name in files for name in ("ruff.toml", ".ruff.toml")) or "ruff" in (files.get("pyproject.toml") or ""):
        found.append("ruff")
    return " + ".join(found) or None


def _ci_checks(files: dict) -> str | None:
    found = []
    for path, text in files.items():
        if not path.replace("\\", "/").startswith(".github/workflows/"):
            continue
        for word in ("test", "lint", "build", "typecheck"):
            if re.search(rf"\b{word}\b", text, re.I) and word not in found:
                found.append(word)
    return ", ".join(found) or None


def _type_checker(files: dict, deps: dict) -> str | None:
    tsconfig = files.get("tsconfig.json")
    if tsconfig:
        if re.search(r'"strict"\s*:\s*true', tsconfig):
            return "tsc strict"
        return "tsc"
    if "mypy.ini" in files or "mypy" in deps or re.search(r"\bmypy\b", files.get("pyproject.toml") or ""):
        return "mypy"
    if "pyrightconfig.json" in files or "pyright" in deps:
        return "pyright"
    return None


def _fact_line(key: str, value: str, fact: dict | None = None) -> str:
    if fact and fact.get("source") == "instruction_file":
        origin = fact.get("origin") or "instruction file"
        return f"This repo's {origin} requires {value}."
    if key == "test_framework":
        return f"This repo uses {value}. Write tests with {value}, not a different framework."
    if key == "language":
        return f"This repo uses {value}."
    if key == "package_manager":
        return f"This repo uses {value}."
    if key == "test_location":
        return f"This repo keeps tests in {value}."
    if key == "linter":
        return f"This repo uses {value}."
    if key == "ci_checks":
        return f"This repo's CI runs {value}."
    if key == "type_checker":
        return f"This repo uses {value}."
    return f"This repo uses {value}."


_CONVENTION_PHRASE = {
    "import_style": {
        "absolute": "Imports are absolute",
        "relative": "Imports are relative",
        "alias": "Imports use an alias such as @/",
    },
    "file_naming": {
        "snake": "snake_case filenames",
        "kebab": "kebab-case filenames",
        "camel": "camelCase filenames",
        "pascal": "PascalCase filenames",
    },
    "error_handling": {
        "throw": "throwing on errors",
        "return_error": "returning error values",
    },
    "test_style": {
        "colocated": "co-located tests",
        "separate": "tests in a separate directory",
        "describe": "describe/it tests",
        "test": "test() tests",
    },
}


def _convention_line(key: str, conv: dict) -> str | None:
    confidence = float(conv.get("confidence") or 0)
    dominant = conv.get("dominant")
    if not dominant or confidence < 0.5:
        return None
    phrase = (_CONVENTION_PHRASE.get(key) or {}).get(dominant, dominant)
    if conv.get("pinned_by_instruction"):
        origin = conv.get("origin") or "instruction file"
        shown = phrase[0].lower() + phrase[1:] if phrase else dominant
        return f"This repo's {origin} requires {shown}."
    if confidence >= 0.8:
        percent = round(confidence * 100)
        if key == "import_style":
            return f"{phrase} in ~{percent}% of this repo. Match that."
        return f"This repo uses {phrase} in ~{percent}% of files."
    if key == "import_style":
        return f"This repo leans toward {phrase[0].lower() + phrase[1:]}."
    return f"This repo leans toward {phrase}."
