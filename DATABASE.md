# PR Sentinel — Database Specification

> **Engine:** SQLite via aiosqlite (async)  
> **Location:** `./sentinel.db` (configurable via `DATABASE_PATH` env var)  
> **Migration strategy:** Schema created on app startup. No Alembic needed for v1.

---

## Schema

### Table: `tasks`

The core entity. One task per agent run per PR.

```sql
CREATE TABLE IF NOT EXISTS tasks (
    -- Identity
    id                  TEXT PRIMARY KEY,                    -- uuid4
    repo                TEXT NOT NULL,                       -- "owner/name"
    pr_number           INTEGER NOT NULL,
    head_sha            TEXT NOT NULL,                       -- SHA that triggered this run

    -- Agent outputs
    severity            TEXT NOT NULL                        -- TRIVIAL|LOW|MEDIUM|HIGH|CRITICAL
                        CHECK(severity IN ('TRIVIAL','LOW','MEDIUM','HIGH','CRITICAL')),
    action              TEXT NOT NULL                        -- skip|comment_only|dispatch|dispatch_urgent
                        CHECK(action IN ('skip','comment_only','dispatch','dispatch_urgent')),
    diagnosis_json      TEXT,                                -- Full DIAGNOSE phase output (JSON string)
    triage_json         TEXT,                                -- Full TRIAGE phase output (JSON string)
    review_markdown     TEXT,                                -- The GitHub comment body
    composer_prompt     TEXT,                                -- Generated Cursor Composer prompt

    -- Blast radius data (denormalized for fast extension reads)
    affected_files      TEXT,                                -- JSON array: [{ path, lines, change_type }]
    blast_radius_json   TEXT,                                -- Full graph: { nodes, edges }
    suggested_fix       TEXT,                                -- One-paragraph fix approach

    -- Lifecycle
    status              TEXT NOT NULL DEFAULT 'pending'      -- pending|dispatched|accepted|in_progress|
                        CHECK(status IN ('pending','dispatched','accepted',  -- resolved|error|dismissed
                                         'in_progress','resolved','error','dismissed')),

    -- GitHub references
    github_comment_id   INTEGER,
    github_comment_url  TEXT,
    github_issue_id     INTEGER,                             -- If dispatch_urgent created an issue

    -- Timestamps (ISO 8601 strings)
    created_at          TEXT NOT NULL,
    dispatched_at       TEXT,
    accepted_at         TEXT,                                -- Extension: dev clicked Accept
    resolved_at         TEXT,
    resolved_sha        TEXT,                                -- SHA of the fix commit

    -- Verification
    verification_json   TEXT,                                -- VERIFY phase output (JSON string)
    is_verified         INTEGER DEFAULT 0                    -- 0 = false, 1 = true (SQLite has no bool)
);

CREATE INDEX IF NOT EXISTS idx_tasks_repo_pr ON tasks(repo, pr_number);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_repo_pending ON tasks(repo, status)
    WHERE status IN ('pending', 'dispatched', 'accepted', 'in_progress');
```

### Table: `trace_steps`

Full agent reasoning chain. One row per Thought/Action/Observation/Answer.

```sql
CREATE TABLE IF NOT EXISTS trace_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL,                       -- 0-indexed within the task
    phase           TEXT NOT NULL                            -- diagnose|triage|dispatch|verify
                    CHECK(phase IN ('diagnose','triage','dispatch','verify')),
    type            TEXT NOT NULL                            -- thought|action|observation|answer|error
                    CHECK(type IN ('thought','action','observation','answer','error')),
    content         TEXT NOT NULL,                           -- The step's text content
    tool_name       TEXT,                                    -- Only if type=action
    tool_args       TEXT,                                    -- JSON string, only if type=action
    elapsed_ms      INTEGER,                                -- Wall-clock time for this step
    created_at      TEXT NOT NULL                            -- ISO 8601
);

CREATE INDEX IF NOT EXISTS idx_trace_task ON trace_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_task_phase ON trace_steps(task_id, phase);
```

### Table: `cache`

Application-level cache for dep graphs, issue bodies, file trees.

```sql
CREATE TABLE IF NOT EXISTS cache (
    cache_key       TEXT PRIMARY KEY,                        -- Format: "type:repo:identifier"
    value           TEXT NOT NULL,                           -- JSON string
    created_at      TEXT NOT NULL,                           -- ISO 8601
    expires_at      TEXT                                     -- ISO 8601 or NULL (never expires)
);
```

**Cache key conventions:**

| Key pattern | What | Expires |
|---|---|---|
| `depgraph:{repo}:{sha}:{path_filter}` | Dependency graph | Never (immutable per SHA) |
| `issue:{repo}:{issue_number}:{updated_at}` | Issue body | Never (keyed by updated_at) |
| `filetree:{repo}:{sha}` | List of files at SHA | Never (immutable per SHA) |

---

## Database Init

```python
# backend/app/database.py

import aiosqlite
from app.config import settings

_db: aiosqlite.Connection | None = None

async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(settings.DATABASE_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")       # Better concurrent reads
        await _db.execute("PRAGMA foreign_keys=ON")
        await _init_schema(_db)
    return _db

async def _init_schema(db: aiosqlite.Connection):
    """Create tables if they don't exist."""
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks ( ... );           -- Full SQL from above
        CREATE TABLE IF NOT EXISTS trace_steps ( ... );
        CREATE TABLE IF NOT EXISTS cache ( ... );
        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_tasks_repo_pr ON tasks(repo, pr_number);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_repo_pending ON tasks(repo, status)
            WHERE status IN ('pending', 'dispatched', 'accepted', 'in_progress');
        CREATE INDEX IF NOT EXISTS idx_trace_task ON trace_steps(task_id);
    """)
    await db.commit()

async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None
```

---

## Query Library

```python
# backend/app/services/task_manager.py

from datetime import datetime, timezone
import json

async def create_task(db, task_id: str, repo, pr_number, head_sha, severity, action,
                      diagnosis, triage, review_md, composer_prompt) -> str:
    """task_id is passed in from the webhook handler — NOT generated here."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("""
        INSERT INTO tasks (id, repo, pr_number, head_sha, severity, action,
                          diagnosis_json, triage_json, review_markdown, composer_prompt,
                          affected_files, blast_radius_json, suggested_fix,
                          status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        task_id, repo, pr_number, head_sha, severity, action,
        json.dumps(diagnosis), json.dumps(triage), review_md, composer_prompt,
        json.dumps(triage.get("affected_files_priority", [])),
        json.dumps(diagnosis.get("blast_radius", {})),
        triage.get("suggested_fix_approach", ""),
        now
    ))
    await db.commit()
    return task_id

async def get_tasks(db, repo: str, statuses: list[str]) -> list[dict]:
    placeholders = ",".join("?" * len(statuses))
    cursor = await db.execute(f"""
        SELECT id, pr_number, severity, status, suggested_fix,
               affected_files, composer_prompt,
               blast_radius_json, created_at
        FROM tasks
        WHERE repo = ? AND status IN ({placeholders})
        ORDER BY created_at DESC
    """, [repo] + statuses)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]

async def get_task(db, task_id: str) -> dict | None:
    cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None

async def update_task_status(db, task_id: str, status: str, **extra_fields) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    timestamp_field = {
        "dispatched": "dispatched_at",
        "accepted": "accepted_at",
        "resolved": "resolved_at",
    }.get(status)

    sets = ["status = ?"]
    vals = [status]

    if timestamp_field:
        sets.append(f"{timestamp_field} = ?")
        vals.append(now)

    for k, v in extra_fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)

    vals.append(task_id)
    result = await db.execute(
        f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals
    )
    await db.commit()
    return result.rowcount > 0

async def get_existing_task(db, repo: str, pr_number: int) -> dict | None:
    """Check if there's already a pending/dispatched task for this PR."""
    cursor = await db.execute("""
        SELECT * FROM tasks
        WHERE repo = ? AND pr_number = ?
          AND status IN ('pending', 'dispatched', 'accepted', 'in_progress')
        ORDER BY created_at DESC LIMIT 1
    """, (repo, pr_number))
    row = await cursor.fetchone()
    return dict(row) if row else None

async def get_reviews(db, repo: str, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    cursor = await db.execute("""
        SELECT id as task_id, repo, pr_number, severity, status,
               json_extract(blast_radius_json, '$.risk_score') as risk_score,
               created_at, resolved_at
        FROM tasks
        WHERE repo = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """, (repo, limit, offset))
    rows = await cursor.fetchall()

    count_cursor = await db.execute(
        "SELECT COUNT(*) FROM tasks WHERE repo = ?", (repo,)
    )
    total = (await count_cursor.fetchone())[0]
    return [dict(r) for r in rows], total

async def get_review_detail(db, task_id: str) -> dict | None:
    task = await get_task(db, task_id)
    if not task:
        return None

    cursor = await db.execute("""
        SELECT step_number, phase, type, content, tool_name, tool_args, elapsed_ms, created_at
        FROM trace_steps
        WHERE task_id = ?
        ORDER BY step_number
    """, (task_id,))
    trace = [dict(r) for r in await cursor.fetchall()]

    task["trace"] = trace
    # Parse JSON fields for the response
    for field in ["diagnosis_json", "triage_json", "blast_radius_json", "affected_files", "verification_json"]:
        if task.get(field):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return task
```

### Trace Step Queries

```python
async def save_trace_step(db, task_id, step_number, phase, type_, content,
                          tool_name=None, tool_args=None, elapsed_ms=None):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("""
        INSERT INTO trace_steps (task_id, step_number, phase, type, content,
                                 tool_name, tool_args, elapsed_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id, step_number, phase, type_, content,
        tool_name, json.dumps(tool_args) if tool_args else None,
        elapsed_ms, now
    ))
    await db.commit()
```

### Cache Queries

```python
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

async def cache_set(db, key: str, value: str, expires_at: str = None):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute("""
        INSERT OR REPLACE INTO cache (cache_key, value, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (key, value, now, expires_at))
    await db.commit()
```

---

## Status Transition Rules

```
Valid transitions:

  pending     → dispatched    (agent posts GitHub comment)
  pending     → error         (agent failed)
  dispatched  → accepted      (extension: dev clicks Accept)
  dispatched  → dismissed     (extension: dev clicks Dismiss)
  accepted    → in_progress   (extension: dev saves affected file)
  in_progress → resolved      (verify phase: fix confirmed)
  in_progress → dispatched    (verify phase: fix incomplete, re-dispatched)
  any         → error         (unrecoverable failure)

Invalid transitions (reject with 400):
  resolved    → anything      (terminal state)
  error       → anything      (terminal state)
  dismissed   → anything      (terminal state)
  pending     → resolved      (can't resolve without going through dispatch)
```

---

## Health Check Query

```python
async def get_health_stats(db) -> dict:
    reviews = await db.execute("SELECT COUNT(*) FROM tasks")
    reviews_count = (await reviews.fetchone())[0]

    last = await db.execute(
        "SELECT created_at FROM tasks ORDER BY created_at DESC LIMIT 1"
    )
    last_row = await last.fetchone()
    last_webhook = last_row["created_at"] if last_row else None

    cache_size = await db.execute(
        "SELECT SUM(LENGTH(value)) FROM cache"
    )
    cache_bytes = (await cache_size.fetchone())[0] or 0

    return {
        "reviews_completed": reviews_count,
        "last_webhook_at": last_webhook,
        "cache_size_kb": round(cache_bytes / 1024, 1),
    }
```
