import aiosqlite

from app.config import settings

_db: aiosqlite.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    head_sha            TEXT NOT NULL,
    severity            TEXT NOT NULL
                        CHECK(severity IN ('TRIVIAL','LOW','MEDIUM','HIGH','CRITICAL')),
    action              TEXT NOT NULL
                        CHECK(action IN ('skip','comment_only','dispatch','dispatch_urgent')),
    diagnosis_json      TEXT,
    triage_json         TEXT,
    review_markdown     TEXT,
    composer_prompt     TEXT,
    affected_files      TEXT,
    blast_radius_json   TEXT,
    suggested_fix       TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','dispatched','accepted',
                                         'in_progress','resolved','error','dismissed')),
    github_comment_id   INTEGER,
    github_comment_url  TEXT,
    github_issue_id     INTEGER,
    created_at          TEXT NOT NULL,
    dispatched_at       TEXT,
    accepted_at         TEXT,
    resolved_at         TEXT,
    resolved_sha        TEXT,
    verification_json   TEXT,
    is_verified         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trace_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL,
    phase           TEXT NOT NULL
                    CHECK(phase IN ('diagnose','triage','dispatch','verify')),
    type            TEXT NOT NULL
                    CHECK(type IN ('thought','action','observation','answer','error')),
    content         TEXT NOT NULL,
    tool_name       TEXT,
    tool_args       TEXT,
    elapsed_ms      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    cache_key       TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_repo_pr ON tasks(repo, pr_number);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_repo_pending ON tasks(repo, status)
    WHERE status IN ('pending', 'dispatched', 'accepted', 'in_progress');
CREATE INDEX IF NOT EXISTS idx_trace_task ON trace_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_task_phase ON trace_steps(task_id, phase);
"""


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(settings.DATABASE_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _init_schema(_db)
    return _db


async def _init_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(_SCHEMA)
    await db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
