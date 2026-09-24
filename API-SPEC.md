# PR Sentinel — API Specification

> **Base URL (local):** `http://localhost:8000`  
> **Base URL (prod):** `https://pr-sentinel-backend.up.railway.app`  
> **Auth:** None (webhook uses HMAC-SHA256 signature verification)

---

## Endpoints

### POST /api/webhook/github

Receives GitHub webhook events. Returns immediately — processing is async.

**Headers:**
```
X-Hub-Signature-256: sha256=<hmac_hex_digest>
Content-Type: application/json
X-GitHub-Event: pull_request
```

**Body:** GitHub webhook payload (see [GitHub docs](https://docs.github.com/en/webhooks/webhook-events-and-payloads#pull_request))

**Behavior:**
1. Verify HMAC-SHA256 signature against `GITHUB_WEBHOOK_SECRET`
2. Filter: only process `pull_request` events with `action` = `opened` or `synchronize`
3. If `synchronize` and an existing task exists for this PR → `verify` mode, **reuse that task's id**
4. If `opened` (or synchronize with no live task) → `full` mode, mint a new `task_id`
5. Return 202 immediately with `task_id` (same id used for SSE, DB row, and extension polling)

**Response (202):**
```json
{ "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890" }
```

**Response (401):**
```json
{ "error": "Invalid webhook signature" }
```

**Response (200 — event filtered out):**
```json
{ "skipped": true, "reason": "Event type not handled" }
```

**Implementation:**
```python
@router.post("/api/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()

    # 1. Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_hmac(body, signature, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(401, "Invalid webhook signature")

    payload = json.loads(body)
    event_type = request.headers.get("X-GitHub-Event", "")

    # 2. Filter
    if event_type != "pull_request":
        return {"skipped": True, "reason": f"Event type: {event_type}"}

    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return {"skipped": True, "reason": f"Action: {action}"}

    # 3. Extract
    pr = payload["pull_request"]
    repo = payload["repository"]["full_name"]
    pr_number = pr["number"]
    head_sha = pr["head"]["sha"]

    # 4. Check for existing task (verify mode)
    db = await get_db()
    existing = await get_existing_task(db, repo, pr_number)

    if action == "synchronize" and existing:
        # VERIFY: reuse the existing task's id — do not mint a new row
        task_id = existing["id"]
        mode = "verify"
    else:
        task_id = str(uuid.uuid4())
        mode = "full"

    asyncio.create_task(
        agent.run(task_id, repo, pr_number, head_sha, mode=mode)
    )

    return JSONResponse({"task_id": task_id}, status_code=202)
```

---

### GET /api/stream/{task_id}

Server-Sent Events stream of agent reasoning steps.

**Response:** `text/event-stream`

**Event format:**
```
data: {"step": 0, "phase": "diagnose", "type": "thought", "content": "I need to fetch the PR diff.", "elapsed_ms": 120}

data: {"step": 1, "phase": "diagnose", "type": "action", "tool": "fetch_pr_diff", "args": {"pr_number": 42}}

data: {"step": 1, "phase": "diagnose", "type": "observation", "content": "+def reset_password(email):..."}

data: {"step": 5, "phase": "diagnose", "type": "answer", "content": "{\"changed_files\": [...]}"}

data: {"step": 6, "phase": "triage", "type": "answer", "content": "{\"severity\": \"HIGH\"}"}

data: {"step": 7, "phase": "dispatch", "type": "answer", "content": "Task dispatched."}
```

**Implementation:**
```python
@router.get("/api/stream/{task_id}")
async def stream_trace(task_id: str):
    async def event_generator():
        async for event in sse_manager.subscribe(task_id):
            yield {"data": json.dumps(event)}
    return EventSourceResponse(event_generator())
```

---

### GET /api/tasks

List tasks for extension polling. Filtered by repo and status.

**Query params:**
- `repo` (required): `owner/name`
- `status` (optional, default: `pending,dispatched`): comma-separated status values

**Response (200):**
```json
{
  "tasks": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "pr_number": 42,
      "severity": "HIGH",
      "status": "pending",
      "suggested_fix": "Add email validation before generating token.",
      "affected_files": [
        { "path": "src/auth.py", "lines": [42, 58], "change_type": "modified" },
        { "path": "src/notifications.py", "lines": [12], "change_type": "impacted" }
      ],
      "composer_prompt": "# PR Sentinel fix request\n\n## What needs to be fixed\n...",
      "blast_radius_summary": "5 downstream dependents, risk 0.65",
      "intent_gaps": ["Validate email format", "Expire token after 1 hour"],
      "created_at": "2026-08-20T14:30:00Z"
    }
  ]
}
```

---

### GET /api/tasks/{task_id}

Full task detail including composer prompt.

**Response (200):** Full task object (all fields from DB).

**Response (404):**
```json
{ "error": "Task not found" }
```

---

### PATCH /api/tasks/{task_id}

Extension reports status changes.

**Body:**
```json
{
  "status": "accepted",
  "extension_version": "0.1.0",
  "editor": "cursor"
}
```

Valid status values from extension: `accepted`, `in_progress`, `dismissed`

**Response (200):**
```json
{ "ok": true }
```

**Response (400):**
```json
{ "error": "Invalid status transition: resolved → accepted" }
```

---

### GET /api/reviews

Dashboard: list past reviews.

**Query params:**
- `repo` (required): `owner/name`
- `limit` (optional, default: 20, max: 100)
- `offset` (optional, default: 0)

**Response (200):**
```json
{
  "reviews": [
    {
      "task_id": "a1b2c3d4...",
      "repo": "amartya-kumar/pr-sentinel-demo",
      "pr_number": 42,
      "severity": "HIGH",
      "status": "resolved",
      "risk_score": 0.65,
      "created_at": "2026-08-20T14:30:00Z",
      "resolved_at": "2026-08-20T15:12:00Z"
    }
  ],
  "total": 47
}
```

---

### GET /api/reviews/{task_id}

Full review detail with trace, blast radius graph, and intent report.

**Response (200):**
```json
{
  "task_id": "a1b2c3d4...",
  "repo": "amartya-kumar/pr-sentinel-demo",
  "pr_number": 42,
  "severity": "HIGH",
  "status": "resolved",
  "diagnosis_json": { "...parsed diagnosis..." },
  "triage_json": { "...parsed triage..." },
  "composer_prompt": "# PR Sentinel fix request\n...",
  "review_markdown": "## 🟠 PR Sentinel Review\n...",
  "blast_radius_json": {
    "nodes": [
      { "id": "auth.validate_token", "file": "src/auth.py", "type": "function", "line": 42 }
    ],
    "edges": [
      { "from": "users.get_user", "to": "auth.validate_token", "type": "calls" }
    ]
  },
  "trace": [
    { "step_number": 0, "phase": "diagnose", "type": "thought", "content": "...", "elapsed_ms": 120 },
    { "step_number": 1, "phase": "diagnose", "type": "action", "tool_name": "fetch_pr_diff", "content": "..." }
  ],
  "jev_confidences": {
    "severity": 0.92,
    "action": 0.88,
    "is_trivial": 0.03,
    "touches_auth": 0.95,
    "risk_level": 2.1
  },
  "requirement_scores": {
    "Validate email format": 0.12,
    "Send reset email": 0.94,
    "Expire token after 1 hour": 0.08
  },
  "created_at": "2026-08-20T14:30:00Z",
  "resolved_at": "2026-08-20T15:12:00Z"
}
```

---

### GET /api/health

**Response (200):**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "reviews_completed": 47,
  "last_webhook_at": "2026-08-20T14:30:00Z",
  "llm_provider": "deepseek-v4-flash",
  "cache_size_kb": 124.5
}
```

---

## HMAC Verification

```python
# backend/app/utils/hmac_verify.py

import hashlib
import hmac

def verify_hmac(body: bytes, signature: str, secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

---

## Error Response Format

All errors follow this shape:
```json
{
  "error": "Human-readable error message",
  "detail": "Optional technical detail"
}
```

HTTP status codes used: 200, 202, 400, 401, 404, 500.
