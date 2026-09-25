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
- **Actual:** Specs + `JevClient` + helpers + DB columns live; orchestrator runs diagnose → Jev triage → dispatch / verify. `JEV_API_KEY` accepted as `TYPESAFE_API_KEY` alias. Per-requirement intent moved to DeepSeek (D-006).

## D-005 — Follow BUILD-SEQUENCE.md phase gates
- **Decision:** Build only via BUILD-SEQUENCE phases; no skip until exit gate passes.
- **Why:** User directed; prevents half-wired agent before services/API land.
- **Advantage:** Clear verify commands; fewer “works on my machine” gaps.
- **Expected:** Phase 0 all 6 checks green → Phase 1 services.
- **Actual:** Phase 0–11 in repo. Backend on Railway, dashboard on Vercel. CI runs mocked backend pytest (10s timeout), dashboard build, extension compile. Demo webhook created after `admin:repo_hook`.

## D-006 — DeepSeek owns intent alignment
- **Decision:** Per-requirement "does the diff implement this?" is a DeepSeek diagnose call. Jev keeps severity, trivial/phantom, and verify scores.
- **Why:** Jev scored "validate email format" at 0.98 from the word "email" in the diff. Lexical match, not implementation.
- **Advantage:** Strict code reading; addressed/missing lists stop false-passing.
- **Expected:** Diagnose JSON `addressed` / `missing` / `scope_creep` from DeepSeek; Jev no longer answers those Nouls.
- **Actual:** Orchestrator calls DeepSeek after the diff + linked issue. Jev triage path unchanged.

## D-007 — Discord + Cursor SDK for the fix loop
- **Decision:** v1 GitHub comment + editor extension stays. v2 adds Discord approve/reject, then a Cursor cloud agent via `cursor-sdk`. Do not delete v1.
- **Why:** V2-DISCORD-CURSOR-SDK-UPDATE — phone can approve, watch, and merge without the laptop.
- **Advantage:** Official SDK (create, stream, cancel, PR). No custom cloud-agent hack.
- **Expected:** Discord notify → approve → cloud agent → diff in Discord → merge → existing verify.
- **Actual:** `backend/app/discord/` plus SDK env (`DISCORD_*`, `CURSOR_API_KEY`) live beside v1. v1 comment/extension path still in tree.
