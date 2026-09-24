# PR Sentinel — Decision Log

Major project decisions only. Update when architecture/product choices change.

Format: **Decision** · **Why** · **Advantage** · **Expected** · **Actual**

---

## D-001 — Spec-first, then code
- **Decision:** Patch PRODUCT-MVP / AGENT-SPEC / API / DATABASE before or with implementation.
- **Why:** Demo acceptance criteria live in those specs; drift breaks the Sep 7 loop.
- **Advantage:** One source of truth; fewer rewrites when triage/webhook rules change.
- **Expected:** Specs match runnable D1–D2 behavior.
- **Actual:** Specs + FastAPI skeleton shipped; agent still stubbed behind webhook.

## D-002 — SQLite + aiosqlite (no Alembic v1)
- **Decision:** Single-file SQLite; create schema on startup; `ALTER` for new columns.
- **Why:** MVP doesn’t need migration tooling; Railway hobby stays simple.
- **Advantage:** Zero ops overhead; fast local verify.
- **Expected:** Tables/indexes ready on boot; safe column adds.
- **Actual:** Works; `jev_confidences` / `requirement_scores` via migrate.

## D-003 — Single `task_id` from webhook
- **Decision:** Webhook mints UUID; `create_task` accepts it; VERIFY reuses live task id.
- **Why:** Dual UUID broke SSE / 202 / extension (PATCH-REPORT bugs 2 & 4).
- **Advantage:** One id across comment → dashboard → Cursor poll.
- **Expected:** `synchronize` returns same `task_id` when a live task exists.
- **Actual:** Day-2 tests pass; reuse works when row exists.

## D-004 — Dual model: DeepSeek + Jev
- **Decision:** DeepSeek = ReAct + narrative; Jev = classification / per-req confidence.
- **Why:** JEV-INTEGRATION-UPDATE — typed decisions, calibrated scores, cheaper triage.
- **Advantage:** Lower cost/latency on triage/verify; Scenario 1 auth+missing → CRITICAL override.
- **Expected:** Specs + client + assemble helpers; full agent loop later.
- **Actual:** Specs + `JevClient` + helpers + DB columns live; orchestrator runs diagnose → Jev triage → dispatch / verify. `JEV_API_KEY` accepted as `TYPESAFE_API_KEY` alias.

## D-005 — Follow BUILD-SEQUENCE.md phase gates
- **Decision:** Build only via BUILD-SEQUENCE phases; no skip until exit gate passes.
- **Why:** User directed; prevents half-wired agent before services/API land.
- **Advantage:** Clear verify commands; fewer “works on my machine” gaps.
- **Expected:** Phase 0 all 6 checks green → Phase 1 services.
- **Actual:** Phase 0–9 PASS. Verify loop: fix push → Jev re-check → `resolved` + comment. Next: Phase 10 deploy.
