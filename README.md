# PR Sentinel

AI-powered PR remediation — from detection to fix to merge, controlled from your phone.

![CI](https://github.com/AmartyaKumar11/PR-Sentinel/actions/workflows/ci.yml/badge.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)
![MIT](https://img.shields.io/badge/license-MIT-green)
![Reviewed by PR Sentinel](https://img.shields.io/badge/reviewed%20by-PR%20Sentinel-blueviolet)

## What is PR Sentinel

PR Sentinel is an AI agent ecosystem that detects issues in GitHub pull requests, diagnoses intent alignment gaps and blast radius, orchestrates an autonomous fix via Cursor Cloud Agent, validates the fix through a quality gate, and lets the developer merge — all from a Discord channel on their phone.

The LLM reasons but never writes code. Cursor's cloud agent handles code generation. PR Sentinel is the brain between GitHub and Cursor: it decides what needs fixing, crafts the prompt, launches the agent, validates the result, and retries automatically if the fix doesn't pass.

Three models, each doing what it's best at: DeepSeek V4 Flash for reasoning (diagnosis, prompt crafting), Jev by TypeSafe AI for fast typed decisions (severity classification, requirement alignment scoring), and Cursor's agent runtime for code generation.

## How it works

```
PR opened on GitHub
     │
     ▼
Webhook → PR Sentinel Backend
     │
     ├─ DIAGNOSE (DeepSeek) ─ fetch diff, issue, build dep graph, trace blast radius
     │
     ├─ TRIAGE (Jev) ─ classify severity, score each requirement
     │
     ├─ NOTIFY → Discord with [Analyze] button
     │
     ▼
Developer taps Approve on phone
     │
     ├─ Cursor Cloud Agent launched with crafted prompt
     │
     ├─ Quality Gate: CI + requirement alignment + diff sanity
     │     │
     │     ├─ PASS → [Merge] button in Discord
     │     └─ FAIL → auto-retry with diagnostic feedback (up to 3x)
     │
     ▼
Developer taps Merge on phone → PR Sentinel verifies → Done
```

## Demo

1. A pull request is opened on the demo repo.
2. Discord pings with the diagnosis (severity and the missing requirements).
3. One tap: Approve. A Cursor cloud agent starts fixing.
4. The agent finishes. The quality gate passes. A Merge button appears.
5. One tap: Merge. PR Sentinel verifies the pull request and marks it resolved.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│   GitHub     │────▶│  PR Sentinel     │────▶│   Discord   │
│  Webhooks    │     │  (FastAPI)       │     │   Bot       │
└─────────────┘     │                  │     └──────┬──────┘
                    │  DeepSeek: reason │            │
                    │  Jev: decide      │     ┌──────▼──────┐
                    │  SQLite: persist  │     │  Developer  │
                    └────────┬─────────┘     │  (phone)    │
                             │               └──────┬──────┘
                    ┌────────▼─────────┐            │
                    │  Cursor Cloud    │◀───────────┘
                    │  Agent (REST)    │  approve / merge
                    └──────────────────┘
```

| Model | Role | Cost |
|-------|------|------|
| DeepSeek V4 Flash | Reasoning: diagnosis, prompt crafting, retry analysis | ~$0.001/review |
| Jev (TypeSafe AI) | Decisions: severity, intent scoring, trivial detection | ~$0.00004/call |
| Cursor Cloud Agent | Code generation: writes the fix, runs tests, opens PR | Cursor Pro subscription |

Specs for the agent, API, database, dashboard, and extension are linked under [Documentation](#documentation).

## Features

- Intent alignment: compares PR changes against linked issue requirements
- Blast radius tracing: Python AST, dependency graph, BFS impact analysis
- Per-requirement confidence scores from Jev
- Cursor Cloud Agent integration via REST API
- Quality gate: CI, requirement alignment, and diff sanity
- Automatic retry loop with diagnostic feedback (up to 3 attempts)
- Natural language Discord control
- Live agent status updates in Discord
- Owner-only access gate
- Draft PR, mark ready, then merge
- VS Code/Cursor extension that writes `.cursor/rules/` context
- React dashboard with a live SSE trace, blast radius graph, and intent report

## Quickstart

Prerequisites: Python 3.11+, Node 20+, and API keys for DeepSeek, Jev (TypeSafe AI), and optionally Cursor Pro.

```bash
git clone https://github.com/AmartyaKumar11/PR-Sentinel.git
cd PR-Sentinel

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in API keys
uvicorn app.main:app --port 8000

# Dashboard
cd ../dashboard
npm install && npm run dev

# Extension
cd ../extension
npm install && npm run compile
npm run package
```

On Windows, activate the backend venv with `.venv\Scripts\activate`.

Point a GitHub repo's webhook at `http://localhost:8000/api/webhook/github`. Use ngrok when GitHub needs HTTPS.

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | Fine-grained PAT with PR, issue, and content permissions |
| `GITHUB_WEBHOOK_SECRET` | Yes | Webhook HMAC secret |
| `DEEPSEEK_API_KEY` | Yes | DeepSeek Platform API key |
| `DEEPSEEK_BASE_URL` | No | Default `https://api.deepseek.com` |
| `TYPESAFE_API_KEY` | Yes | TypeSafe AI API key. `JEV_API_KEY` is accepted as an alias |
| `JEV_MODEL` | No | Default `jev-1.13.0`. Pin the version |
| `DISCORD_BOT_TOKEN` | For Discord | Discord bot token |
| `DISCORD_CHANNEL_ID` | For Discord | Channel for notifications |
| `DISCORD_GUILD_ID` | For Discord | Server ID |
| `DISCORD_OWNER_ID` | For Discord | Your Discord user ID |
| `CURSOR_API_KEY` | For the agent | Cursor Dashboard API key |
| `CURSOR_DEFAULT_MODEL` | No | Default `auto` |

## Project structure

```
PR-Sentinel/
├── backend/
│   ├── app/
│   │   ├── agent/          # Orchestrator, prompts, parser, tools
│   │   ├── discord/        # Bot, commands, views, embeds
│   │   ├── routes/         # FastAPI endpoints
│   │   └── services/       # GitHub, Cursor, LLM, Jev, cache, quality gate
│   ├── tests/
│   └── test_full_pipeline.py
├── dashboard/              # React + Vite + Tailwind
└── extension/              # VS Code/Cursor extension (TypeScript)
```

## Deployment

| Piece | Where |
|-------|--------|
| Backend | Railway |
| Dashboard | Vercel |
| Extension | VSIX sideload into Cursor |

## Cost per review

| Component | Cost |
|-----------|------|
| Diagnosis (DeepSeek, 3–5 calls) | ~$0.001 |
| Triage (Jev, 1 call) | ~$0.00004 |
| Prompt crafting (DeepSeek, 1 call) | ~$0.0005 |
| Quality gate (Jev, 1 call) | ~$0.00004 |
| Discord handler (per message) | ~$0.00014 |
| **Total per review** | **~$0.002** |
| Cursor Cloud Agent | Cursor Pro subscription |

A $20 budget covers about 10,000 reviews, before the Cursor subscription.

## Testing

```bash
cd backend && pytest tests/ -v --timeout=10
```

Full pipeline against a live pull request (no Discord):

```bash
cd backend && python test_full_pipeline.py AmartyaKumar11 pr-sentinel-demo 8
```

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.11, FastAPI, aiosqlite |
| LLM (reasoning) | DeepSeek V4 Flash via the OpenAI SDK |
| LLM (decisions) | Jev 1.13.0 via the TypeSafe SDK |
| Code agent | Cursor Cloud Agent via REST |
| Database | SQLite (WAL mode) |
| Dashboard | React 19, Vite, Tailwind, React Flow |
| Extension | TypeScript, VS Code Extension API |
| Bot | discord.py |
| Deploy | Railway (backend), Vercel (dashboard) |
| CI | GitHub Actions |

## Documentation

Architecture, agent prompts, the database schema, API contracts, and the build sequence:

- [Agent spec](AGENT-SPEC.md)
- [API spec](API-SPEC.md)
- [Database](DATABASE.md)
- [Dashboard spec](DASHBOARD-SPEC.md)
- [Extension spec](EXTENSION-SPEC.md)
- [Build sequence](BUILD-SEQUENCE.md)
- [Setup](SETUP.md)

## Author

Built by [Amartya Kumar](https://github.com/AmartyaKumar11).

## License

[MIT](LICENSE)
