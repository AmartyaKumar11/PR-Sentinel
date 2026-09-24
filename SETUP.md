# PR Sentinel — Setup Guide

> **Read this first.** This document bootstraps the entire project from zero.

---

## Prerequisites

```
Node.js     >= 20.x    (dashboard + extension)
Python      >= 3.11    (backend)
npm         >= 10.x
pip         (ships with Python)
Git         >= 2.40
Docker      >= 24.x    (local dev only)
ngrok       or cloudflared (webhook tunnel for local dev)
```

### Accounts Required

| Service | What For | Free Tier? |
|---|---|---|
| [GitHub](https://github.com) | Source hosting, webhooks, API | Yes |
| [DeepSeek Platform](https://platform.deepseek.com) | LLM API key | 5M free tokens on signup |
| [Railway](https://railway.app) | Backend deploy | $5/mo hobby (free trial) |
| [Vercel](https://vercel.com) | Dashboard deploy | Yes |
| [Open VSX](https://open-vsx.org) | Extension publish (optional) | Yes |

---

## 1. Repository Setup

```bash
mkdir pr-sentinel && cd pr-sentinel
git init

# Create project structure
mkdir -p backend/app/{routes,agent/tools,services,models,utils}
mkdir -p backend/tests/fixtures/sample_files
mkdir -p dashboard/src/{api,components,hooks,pages,styles}
mkdir -p extension/src/{api,providers,views,utils}
mkdir -p demo-repo/src demo-repo/tests

# Create placeholder files
touch backend/app/__init__.py
touch backend/app/routes/__init__.py
touch backend/app/agent/__init__.py
touch backend/app/agent/tools/__init__.py
touch backend/app/services/__init__.py
touch backend/app/models/__init__.py
touch backend/app/utils/__init__.py
touch backend/tests/__init__.py
```

## 2. Backend Setup

### 2.1 Python Environment

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
```

### 2.2 Dependencies

Create `backend/requirements.txt`:

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pydantic>=2.9.0
pydantic-settings>=2.5.0
openai>=1.50.0
sse-starlette>=2.0.0
python-dotenv>=1.0.0
aiosqlite>=0.20.0
typesafe-sdk>=0.1.0
```

```bash
pip install -r requirements.txt
```

### 2.3 Environment Variables

Create `backend/.env`:

```env
# ─── GitHub ───
GITHUB_WEBHOOK_SECRET=dev_secret_change_in_prod
GITHUB_TOKEN=ghp_YOUR_FINE_GRAINED_PAT

# ─── DeepSeek V4 Flash ───
DEEPSEEK_API_KEY=sk-YOUR_DEEPSEEK_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_MAX_TOKENS=4096
LLM_TEMPERATURE=0.1

# ─── Jev (TypeSafe AI) ───
TYPESAFE_API_KEY=sk-xxxx
JEV_MODEL=jev-1.13.0

# ─── App ───
APP_PORT=8000
FRONTEND_URL=http://localhost:5173
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
DATABASE_PATH=./sentinel.db
LOG_LEVEL=debug
```
### 2.4 GitHub Fine-Grained PAT

Create at: `GitHub → Settings → Developer settings → Fine-grained tokens`

Required permissions (on the demo-repo only):
- `Contents: Read`
- `Issues: Read and Write`
- `Pull requests: Read and Write`

### 2.5 Start Backend (Dev)

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2.6 Webhook Tunnel (Local Dev)

```bash
# Terminal 2
ngrok http 8000
# Note the https URL → set as GitHub webhook endpoint
# https://xxxx.ngrok-free.app/api/webhook/github
```

GitHub webhook setup:
1. Demo-repo → Settings → Webhooks → Add webhook
2. Payload URL: `{ngrok_url}/api/webhook/github`
3. Content type: `application/json`
4. Secret: same as `GITHUB_WEBHOOK_SECRET` in .env
5. Events: select "Pull requests" and "Pushes"

---

## 3. Dashboard Setup

```bash
cd dashboard
npm create vite@latest . -- --template react
npm install
npm install axios react-router-dom @xyflow/react dagre
npm install -D tailwindcss @tailwindcss/vite
```

Create `dashboard/vite.config.js`:
```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

Add to `dashboard/src/styles/index.css` top:
```css
@import "tailwindcss";
```

```bash
cd dashboard
npm run dev
```

---

## 4. Extension Setup

```bash
cd extension
npm init -y
npm install -D @types/vscode typescript webpack webpack-cli ts-loader
```

Create `extension/tsconfig.json`:
```json
{
  "compilerOptions": {
    "module": "commonjs",
    "target": "ES2020",
    "outDir": "dist",
    "lib": ["ES2020"],
    "sourceMap": true,
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "resolveJsonModule": true
  },
  "exclude": ["node_modules", "dist"]
}
```

Create `extension/webpack.config.js`:
```javascript
const path = require('path');
module.exports = {
  target: 'node',
  mode: 'production',
  entry: './src/extension.ts',
  output: { path: path.resolve(__dirname, 'dist'), filename: 'extension.js', libraryTarget: 'commonjs2' },
  externals: { vscode: 'commonjs vscode' },
  resolve: { extensions: ['.ts', '.js'] },
  module: { rules: [{ test: /\.ts$/, use: 'ts-loader', exclude: /node_modules/ }] },
};
```

---

## 5. Docker Compose (Local Dev)

Create `docker-compose.yml` at project root:

```yaml
version: "3.9"
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    env_file: backend/.env
    volumes:
      - ./backend/app:/app/app
      - sentinel_data:/app/data
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  dashboard:
    build: ./dashboard
    ports:
      - "5173:5173"
    volumes:
      - ./dashboard/src:/app/src
    environment:
      - VITE_API_URL=http://localhost:8000
    command: npm run dev -- --host

volumes:
  sentinel_data:
```

Create `backend/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker compose up
```

---

## 6. Demo Repo Setup

Create a SEPARATE GitHub repository called `pr-sentinel-demo`:

```bash
mkdir pr-sentinel-demo && cd pr-sentinel-demo
git init
# Create the 6 source files (see DEMO-REPO.md for contents)
# Create the 3 test files
# Push to GitHub
# Enable webhook pointing to PR Sentinel backend
```

---

## 7. Verify Everything Works

```bash
# Backend health
curl http://localhost:8000/api/health
# Expected: {"status":"ok","uptime_seconds":...}

# DeepSeek API test
python -c "
from openai import OpenAI
c = OpenAI(api_key='YOUR_KEY', base_url='https://api.deepseek.com')
r = c.chat.completions.create(model='deepseek-v4-flash', messages=[{'role':'user','content':'Say hello'}], max_tokens=50)
print(r.choices[0].message.content)
"

# Dashboard
open http://localhost:5173
```

---

## File Map (What Goes Where)

```
pr-sentinel/
├── docs/                          ← YOU ARE HERE
│   ├── SETUP.md                   ← This file
│   ├── AGENT-SPEC.md              ← Agent phases, prompts, tool schemas
│   ├── PRODUCT-MVP.md             ← MVP scope, acceptance criteria
│   ├── DATABASE.md                ← Schema, queries, migrations
│   ├── API-SPEC.md                ← REST endpoints, req/res examples
│   ├── EXTENSION-SPEC.md          ← VS Code extension architecture
│   └── DASHBOARD-SPEC.md          ← React components, SSE, visualization
├── backend/                       ← FastAPI + Agent
├── dashboard/                     ← React + Vite
├── extension/                     ← VS Code/Cursor extension
├── demo-repo/                     ← (separate GitHub repo)
├── docker-compose.yml
└── README.md
```
