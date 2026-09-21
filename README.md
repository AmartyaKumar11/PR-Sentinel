# PR Sentinel

AI-powered PR remediation: diagnose intent + blast radius, stream reasoning, dispatch a Cursor task, verify the fix.

## Specs

See the markdown docs at the repo root (`PRODUCT-MVP.md`, `AGENT-SPEC.md`, etc.).

## Backend (local)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill GITHUB_TOKEN / DEEPSEEK_API_KEY
$env:PYTHONPATH = (Get-Location).Path
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify:

```powershell
# Health
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())"

# Analysis self-check
$env:PYTHONPATH = (Get-Location).Path
python tests/test_analysis.py
```

## Status

Shipped so far:

- M-01 health
- M-02 webhook HMAC + filter (agent stub) — acceptance tested
- M-03 GitHub client + tool registry — mocked + live README fetch
- M-04 / M-05 / M-06 dep graph, diff parser, blast radius
- Task / review / SSE routes (empty until agent persists tasks)

```powershell
cd backend
$env:PYTHONPATH = (Get-Location).Path
python tests/test_analysis.py
python tests/test_day2.py
```

Next: agent DIAGNOSE → TRIAGE → DISPATCH (M-07..M-09).
