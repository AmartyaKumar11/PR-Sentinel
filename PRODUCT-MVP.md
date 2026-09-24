# PR Sentinel — Product MVP Specification

> **MVP deadline:** Sep 7, 2026  
> **Principle:** Ship the narrowest version that demonstrates the full loop.

---

## MVP Definition

The MVP is the smallest version of PR Sentinel that demonstrates ALL of:
1. AI agent analyzing a PR (intent + blast radius)
2. Live reasoning trace visible on a dashboard
3. Structured task delivered to a developer's IDE
4. Cursor context injection (`.cursor/rules/`)
5. Fix verification on follow-up push

If ANY of these are missing, the project doesn't demonstrate its thesis.

---

## What's IN the MVP

### P0 — Must Ship (blocks the demo)

| ID | Feature | Acceptance Criteria | Day |
|---|---|---|---|
| M-01 | FastAPI backend with health check | `GET /api/health` returns 200 with uptime | D1 |
| M-02 | GitHub webhook receiver with HMAC verification | Valid webhook → 202; invalid signature → 401; non-PR event → 200 skipped | D2 |
| M-03 | GitHub client: fetch diff, issue, file content, post comment | Each method returns correct data from a real GitHub repo | D2 |
| M-04 | Python AST parser → dependency graph | Given demo repo's 6 .py files, produces correct nodes and edges | D3 |
| M-05 | Diff parser → changed identifiers | Given a unified diff, extracts function/class names that changed | D3 |
| M-06 | Blast radius tracer (BFS + risk score) | Given demo repo graph + changed identifiers, returns correct impact chain and score | D3 |
| M-07 | Agent DIAGNOSE phase | Given a PR number, completes diagnosis in ≤5 iterations with correct JSON output | D4 |
| M-08 | Agent TRIAGE phase | Given a diagnosis, classifies severity correctly for all 4 demo scenarios | D4 |
| M-09 | Agent DISPATCH phase | Creates task in DB, posts GitHub comment, generates Composer prompt | D5 |
| M-10 | SSE streaming of agent trace | Dashboard can subscribe and receive real-time events during an agent run | D5 |
| M-11 | Task API for extension | `GET /api/tasks` returns pending tasks filtered by repo; `PATCH` updates status | D5 |
| M-12 | Demo repo with 4 scenarios | 4 branches, each produces expected agent output | D6 |
| M-13 | Dashboard: PR review list | Shows past reviews with severity badges, sorted by recency | D8 |
| M-14 | Dashboard: live agent trace viewer | Streams reasoning steps in real-time during an active agent run | D9 |
| M-15 | Dashboard: blast radius graph | React Flow visualization with depth-colored nodes and animated propagation | D10 |
| M-16 | Dashboard: intent alignment report | Shows addressed/missing/scope creep with issue reference | D10 |
| M-17 | Extension: task polling + notification | Polls backend, shows VS Code notification on new task | D11 |
| M-18 | Extension: file navigator + context injection | Opens affected files at correct lines; writes `.cursor/rules/sentinel-context.mdc` | D12 |
| M-19 | Agent VERIFY phase | On follow-up push to a tracked PR, re-analyzes and updates task status | D13 |
| M-20 | Deploy backend to Railway | Production URL serves API | D15 |
| M-21 | Deploy dashboard to Vercel | Production URL serves React app | D15 |

### P1 — Should Ship (improves demo, not blocking)

| ID | Feature | Day |
|---|---|---|
| P1-01 | Extension: blast radius sidebar webview | D13 |
| P1-02 | Extension: status reporter (file save → backend) | D12 |
| P1-03 | Dashboard: task lifecycle tracking view | D13 |
| P1-04 | Dashboard: Composer prompt viewer with copy button | D10 |
| P1-05 | GitHub issue creation for Background Agent path | D16 |
| P1-06 | Extension VSIX packaging for Cursor sideload | D16 |
| P1-07 | GitHub Actions CI (tests + lint + build) | D17 |
| P1-08 | Dogfooding: PR Sentinel reviews its own PRs | D17 |
| P1-09 | Cross-device testing (Cursor, VS Code, mobile) | D19 |

### P2 — Won't Ship (noted for future)

| ID | Feature | Why Deferred |
|---|---|---|
| P2-01 | JS/TS AST support via tree-sitter | Separate parser needed, different import resolution |
| P2-02 | Cross-PR conflict detection | Different feature entirely |
| P2-03 | Convention drift detection | Requires training/indexing phase |
| P2-04 | GitLab/Bitbucket support | Different webhook format + API |
| P2-05 | Team analytics dashboard | Needs data accumulation over weeks |
| P2-06 | Auto-fix code generation by the LLM | Out of scope — Cursor handles this |

---

## Demo Scenarios — Expected Outputs

### Scenario 1: Missing Requirements + High Blast Radius
- **Branch:** `feature/1-password-reset`
- **Issue #1:** "Add password reset: validate email format, send reset email, expire token after 1 hour"
- **PR changes:** Adds `reset_password()` in auth.py — does NOT validate email, does NOT expire token
- **Expected diagnosis:**
  - `missing`: ["Validate email format", "Expire token after 1 hour"]
  - `addressed`: ["Send reset email"]
  - `scope_creep`: []
  - `risk_score`: ≥ 0.5 (auth.py touches users, orders, admin downstream)
- **Expected triage:** severity=CRITICAL, action=dispatch_urgent
- **Expected Jev output:**
  - severity: CRITICAL (confidence: ~0.89)
  - action: dispatch_urgent (confidence: ~0.91)
  - touches_auth: ~0.95
  - Requirement scores:
    - "Validate email format": ~0.1 (NOT addressed)
    - "Send reset email": ~0.9 (addressed)
    - "Expire token after 1 hour": ~0.1 (NOT addressed)
  - risk_level: ~2.5/3 (high)

### Scenario 2: Scope Creep
- **Branch:** `fix/2-order-validation`
- **Issue #2:** "Reject negative quantities in create_order"
- **PR changes:** Fixes validation in orders.py BUT also refactors send_email in notifications.py
- **Expected diagnosis:**
  - `missing`: []
  - `addressed`: ["Reject negative quantities"]
  - `scope_creep`: ["Refactored send_email in notifications.py — not in issue"]
- **Expected triage:** severity=MEDIUM, action=dispatch

### Scenario 3: Phantom PR
- **Branch:** `feature/no-issue-billing`
- **No linked issue**
- **PR changes:** Modifies billing.py
- **Expected diagnosis:**
  - `linked_issue`: null
  - `is_phantom_pr`: true
  - `scope_creep`: non-empty (every change is untracked work)
- **Expected triage:** severity=MEDIUM, action=dispatch

### Scenario 4: Trivial PR
- **Branch:** `docs/update-readme`
- **PR changes:** Only modifies README.md
- **Expected diagnosis:**
  - `is_trivial`: true
  - All blast radius fields empty/zero
- **Expected triage:** severity=TRIVIAL, action=skip

---

## Success Criteria for MVP

The MVP is "done" when Amartya can execute this demo flow without any manual intervention:

1. Push `feature/1-password-reset` branch as a PR to demo-repo
2. Within 30 seconds, dashboard shows the agent reasoning in real-time
3. Agent correctly identifies 2 missing requirements and flags CRITICAL severity
4. GitHub PR gets a structured review comment
5. In Cursor, extension notification pops up with the task
6. Click Accept → files open at correct lines
7. `.cursor/rules/sentinel-context.mdc` exists with correct diagnosis
8. Open Cursor Composer → context is loaded → can execute the prompt
9. Push a fix commit
10. PR Sentinel re-verifies → marks task as resolved
11. Dashboard shows the full lifecycle: pending → dispatched → accepted → resolved

**Time budget for the demo:** Under 5 minutes including the fix step.
