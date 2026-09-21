from datetime import datetime, timezone
import json

# Valid transitions from DATABASE.md
_TRANSITIONS = {
    "pending": {"dispatched", "error"},
    "dispatched": {"accepted", "dismissed", "error"},
    "accepted": {"in_progress", "error"},
    "in_progress": {"resolved", "dispatched", "error"},
}


async def create_task(
    db,
    task_id: str,
    repo,
    pr_number,
    head_sha,
    severity,
    action,
    diagnosis,
    triage,
    review_md,
    composer_prompt,
) -> str:
    """task_id is passed in from the webhook handler — NOT generated here."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO tasks (id, repo, pr_number, head_sha, severity, action,
                          diagnosis_json, triage_json, review_markdown, composer_prompt,
                          affected_files, blast_radius_json, suggested_fix,
                          status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            task_id,
            repo,
            pr_number,
            head_sha,
            severity,
            action,
            json.dumps(diagnosis),
            json.dumps(triage),
            review_md,
            composer_prompt,
            json.dumps(triage.get("affected_files_priority", [])),
            json.dumps(diagnosis.get("blast_radius", {})),
            triage.get("suggested_fix_approach", ""),
            now,
        ),
    )
    await db.commit()
    return task_id


async def get_tasks(db, repo: str, statuses: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    cursor = await db.execute(
        f"""
        SELECT id, pr_number, severity, status, suggested_fix,
               affected_files, composer_prompt,
               blast_radius_json, diagnosis_json, created_at
        FROM tasks
        WHERE repo = ? AND status IN ({placeholders})
        ORDER BY created_at DESC
        """,
        [repo] + statuses,
    )
    rows = await cursor.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # Derive extension-facing fields
        br = {}
        try:
            br = json.loads(d.get("blast_radius_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        impact = len(br.get("depth_1_impacted", [])) + len(br.get("depth_2_impacted", []))
        d["blast_radius_summary"] = f"{impact} downstream dependents, risk {br.get('risk_score', 0)}"
        intent_gaps = []
        try:
            diag = json.loads(d.get("diagnosis_json") or "{}")
            intent_gaps = diag.get("intent_alignment", {}).get("missing", [])
        except (json.JSONDecodeError, TypeError):
            pass
        d["intent_gaps"] = intent_gaps
        if d.get("affected_files"):
            try:
                d["affected_files"] = json.loads(d["affected_files"])
            except (json.JSONDecodeError, TypeError):
                pass
        d.pop("blast_radius_json", None)
        d.pop("diagnosis_json", None)
        out.append(d)
    return out


async def get_task(db, task_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_task_status(db, task_id: str, status: str, **extra_fields) -> bool:
    task = await get_task(db, task_id)
    if not task:
        return False
    current = task["status"]
    allowed = _TRANSITIONS.get(current, set())
    # any → error is always ok
    if status != "error" and status not in allowed and current != status:
        raise ValueError(f"Invalid status transition: {current} → {status}")

    now = datetime.now(timezone.utc).isoformat()
    timestamp_field = {
        "dispatched": "dispatched_at",
        "accepted": "accepted_at",
        "resolved": "resolved_at",
    }.get(status)

    sets = ["status = ?"]
    vals: list = [status]

    if timestamp_field:
        sets.append(f"{timestamp_field} = ?")
        vals.append(now)

    for k, v in extra_fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)

    vals.append(task_id)
    result = await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return result.rowcount > 0


async def get_existing_task(db, repo: str, pr_number: int) -> dict | None:
    cursor = await db.execute(
        """
        SELECT * FROM tasks
        WHERE repo = ? AND pr_number = ?
          AND status IN ('pending', 'dispatched', 'accepted', 'in_progress')
        ORDER BY created_at DESC LIMIT 1
        """,
        (repo, pr_number),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_reviews(db, repo: str, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    cursor = await db.execute(
        """
        SELECT id as task_id, repo, pr_number, severity, status,
               json_extract(blast_radius_json, '$.risk_score') as risk_score,
               created_at, resolved_at
        FROM tasks
        WHERE repo = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (repo, limit, offset),
    )
    rows = await cursor.fetchall()
    count_cursor = await db.execute("SELECT COUNT(*) FROM tasks WHERE repo = ?", (repo,))
    total = (await count_cursor.fetchone())[0]
    return [dict(r) for r in rows], total


async def get_review_detail(db, task_id: str) -> dict | None:
    task = await get_task(db, task_id)
    if not task:
        return None

    cursor = await db.execute(
        """
        SELECT step_number, phase, type, content, tool_name, tool_args, elapsed_ms, created_at
        FROM trace_steps
        WHERE task_id = ?
        ORDER BY step_number
        """,
        (task_id,),
    )
    trace = [dict(r) for r in await cursor.fetchall()]
    task["trace"] = trace
    task["task_id"] = task["id"]
    for field in [
        "diagnosis_json",
        "triage_json",
        "blast_radius_json",
        "affected_files",
        "verification_json",
    ]:
        if task.get(field):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return task


async def save_trace_step(
    db,
    task_id,
    step_number,
    phase,
    type_,
    content,
    tool_name=None,
    tool_args=None,
    elapsed_ms=None,
):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO trace_steps (task_id, step_number, phase, type, content,
                                 tool_name, tool_args, elapsed_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            step_number,
            phase,
            type_,
            content,
            tool_name,
            json.dumps(tool_args) if tool_args else None,
            elapsed_ms,
            now,
        ),
    )
    await db.commit()


async def cache_get(db, key: str) -> str | None:
    cursor = await db.execute(
        "SELECT value, expires_at FROM cache WHERE cache_key = ?", (key,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat():
        await db.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
        await db.commit()
        return None
    return row["value"]


async def cache_set(db, key: str, value: str, expires_at: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT OR REPLACE INTO cache (cache_key, value, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (key, value, now, expires_at),
    )
    await db.commit()


async def get_health_stats(db) -> dict:
    reviews = await db.execute("SELECT COUNT(*) FROM tasks")
    reviews_count = (await reviews.fetchone())[0]

    last = await db.execute(
        "SELECT created_at FROM tasks ORDER BY created_at DESC LIMIT 1"
    )
    last_row = await last.fetchone()
    last_webhook = last_row["created_at"] if last_row else None

    cache_size = await db.execute("SELECT SUM(LENGTH(value)) FROM cache")
    cache_bytes = (await cache_size.fetchone())[0] or 0

    return {
        "reviews_completed": reviews_count,
        "last_webhook_at": last_webhook,
        "cache_size_kb": round(cache_bytes / 1024, 1),
    }
