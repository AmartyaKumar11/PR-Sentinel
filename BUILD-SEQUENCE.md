# PR Sentinel — Build Sequence

> **Purpose:** Cursor reads this file and builds the project phase by phase. Each phase has exact file paths, implementation details, verification commands, and an exit gate. Do NOT skip phases. Do NOT move to phase N+1 until phase N's exit gate passes.  
> **Author:** Amartya Kumar  
> **Reference docs:** AGENT-SPEC.md, DATABASE.md, API-SPEC.md, PRODUCT-MVP.md, EXTENSION-SPEC.md, DASHBOARD-SPEC.md, DEMO-REPO.md, JEV-INTEGRATION-UPDATE.md, PATCH-REPORT.md  
> **LLM stack:** DeepSeek V4 Flash (reasoning) + Jev 1.13.0 (decisions)

---

## How to Use This File

```
1. Start at Phase 0.
2. Read the phase's BUILD section. Implement everything listed.
3. Run the VERIFY section. Every check must pass.
4. Only then move to the next phase.
5. If a verify step fails, fix it before continuing.
6. Reference the spec MDs for schemas, prompts, and API contracts — don't reinvent.
```

---

## Phase 0: Verify Current State

**Purpose:** Confirm what's already built before building anything new.

### VERIFY

Run each of these. Record what passes and what fails. Fix failures before Phase 1.

```bash
# 1. Server starts
cd backend
source .venv/bin/activate  # or however the venv is activated
uvicorn app.main:app --port 8000 &
sleep 3
curl -s http://localhost:8000/api/health | python -m json.tool
# EXPECT: {"status": "ok", "uptime_seconds": ..., ...}

# 2. Database tables exist
python -c "
import sqlite3
conn = sqlite3.connect('sentinel.db')
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print([t[0] for t in tables])
# EXPECT: ['tasks', 'trace_steps', 'cache'] (at minimum)
"

# 3. Tasks table has jev columns
python -c "
import sqlite3
conn = sqlite3.connect('sentinel.db')
cols = [c[1] for c in conn.execute('PRAGMA table_info(tasks)').fetchall()]
assert 'jev_confidences' in cols, 'Missing jev_confidences column'
assert 'requirement_scores' in cols, 'Missing requirement_scores column'
print('Jev columns OK')
"

# 4. DeepSeek API key works
python -c "
from openai import OpenAI
from app.config import settings
c = OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL)
r = c.chat.completions.create(model=settings.LLM_MODEL, messages=[{'role':'user','content':'Say OK'}], max_tokens=10)
print('DeepSeek:', r.choices[0].message.content)
# EXPECT: Some response containing 'OK'
"

# 5. Jev API key works
python -c "
from typesafe_sdk import TypeSafeClient, Noul
c = TypeSafeClient()
r = c.system_one(state='The sky is blue.', questions={'is_true': Noul(instructions='The statement is factually correct')})
print('Jev noul:', r.answers['is_true'].noul)
# EXPECT: A float close to 1.0
"

# 6. Config loads all required vars
python -c "
from app.config import settings
assert settings.GITHUB_WEBHOOK_SECRET, 'Missing GITHUB_WEBHOOK_SECRET'
assert settings.GITHUB_TOKEN, 'Missing GITHUB_TOKEN'
assert settings.DEEPSEEK_API_KEY, 'Missing DEEPSEEK_API_KEY'
assert settings.TYPESAFE_API_KEY or settings.JEV_API_KEY, 'Missing Jev key'
print('Config OK')
"
```

### EXIT GATE

All 6 checks pass. If any fail, fix before Phase 1.

---

## Phase 1: Core Services — GitHub Client, Diff Parser, AST Parser, Blast Radius

**Purpose:** Build the four data-gathering services that the agent's tools call. No agent logic yet — just the services that fetch and process data.

### BUILD

#### 1.1 GitHub Client (`backend/app/services/github_client.py`)

If this file already exists with working methods, verify and skip to 1.2. If it's missing or incomplete, implement:

```python
"""
GitHub REST API wrapper. All methods are async.
Uses httpx with the GITHUB_TOKEN from config.
Base URL: https://api.github.com
"""

import httpx
from app.config import settings

class GitHubClient:
    def __init__(self):
        self.base = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """
        Fetch PR diff as unified diff text.
        Uses the 'application/vnd.github.v3.diff' accept header.
        Returns the raw diff string, truncated to ~12000 chars (~3000 tokens).
        """
        # GET /repos/{owner}/{repo}/pulls/{pr_number}
        # Accept: application/vnd.github.v3.diff
        # Truncate: if len > 12000, keep first 6000 + last 6000 with "... truncated ..." in middle
        ...

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """
        Fetch list of files changed in PR with patch hunks.
        GET /repos/{owner}/{repo}/pulls/{pr_number}/files
        Returns list of {filename, status, additions, deletions, patch}
        """
        ...

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> dict:
        """
        Fetch PR metadata (body, head sha, branch name, title).
        GET /repos/{owner}/{repo}/pulls/{pr_number}
        Returns {title, body, head_sha, branch, state}
        """
        ...

    async def get_linked_issue(self, owner: str, repo: str, pr_number: int) -> dict | None:
        """
        Find the linked issue by:
        1. Scanning PR body for #N references (regex: r'#(\d+)')
        2. Scanning branch name for patterns: feature/N-desc, fix/N-desc, issue-N
        3. If found: GET /repos/{owner}/{repo}/issues/{N}
        4. Return {number, title, body, labels, state} or None
        
        Truncate issue body to first 2000 chars (~500 tokens).
        """
        ...

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        """
        Fetch raw file content at a specific git ref.
        GET /repos/{owner}/{repo}/contents/{path}?ref={ref}
        Base64 decode the content field.
        If file > 200 lines, find the function/class containing line 100 and
        return only that block with 10 lines context above and below.
        """
        ...

    async def get_file_tree(self, owner: str, repo: str, ref: str, path_filter: str = "") -> list[str]:
        """
        Fetch the list of file paths in the repo at a ref.
        GET /repos/{owner}/{repo}/git/trees/{ref}?recursive=1
        Filter to only files matching path_filter prefix.
        Filter to only .py files (for AST parsing).
        Returns list of file paths.
        """
        ...

    async def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict:
        """
        Post a comment on a PR (uses the issues API).
        POST /repos/{owner}/{repo}/issues/{pr_number}/comments
        Body: {"body": body}
        Returns {id, html_url}
        """
        ...

    async def create_issue(self, owner: str, repo: str, title: str, body: str, labels: list[str] = None) -> dict:
        """
        Create a GitHub issue (for Background Agent path).
        POST /repos/{owner}/{repo}/issues
        Returns {number, id, html_url}
        """
        ...
```

#### 1.2 Diff Parser (`backend/app/services/diff_parser.py`)

```python
"""
Parse unified diffs to extract changed identifiers.
Input: unified diff text from GitHub API
Output: list of changed file paths + list of changed function/class identifiers
"""

import re

def parse_diff(diff_text: str) -> dict:
    """
    Parse unified diff into structured data.
    
    Returns {
        "files": [
            {
                "path": "src/auth.py",
                "status": "modified",     # modified | added | deleted | renamed
                "hunks": [
                    {"start_line": 42, "end_line": 58, "content": "..."}
                ]
            }
        ],
        "summary": "3 files changed, +47 -12"
    }
    """
    ...

def extract_changed_identifiers(diff_data: dict, dep_graph: dict = None) -> list[str]:
    """
    From the parsed diff, extract function/class names that were changed.
    
    Strategy:
    1. For each hunk in each file, get the changed line numbers
    2. If dep_graph is provided: cross-reference line numbers with
       node locations from the dep graph to find which functions were modified
    3. If no dep_graph: regex scan the diff for 'def ' and 'class ' in added/modified lines
    4. Return identifiers in "module.function_name" format
       e.g. ["auth.validate_token", "auth.reset_password"]
    
    The module name is derived from the file path:
    "src/auth.py" → "auth"
    "src/utils/helpers.py" → "utils.helpers"
    """
    ...

def is_code_file(path: str) -> bool:
    """Check if a file path is a Python source file."""
    return path.endswith('.py') and not path.startswith('test') and '__pycache__' not in path

def is_test_file(path: str) -> bool:
    """Check if a file path is a test file."""
    return path.endswith('.py') and ('test_' in path.split('/')[-1] or path.split('/')[-1].startswith('test'))
```

#### 1.3 AST Parser (`backend/app/services/ast_parser.py`)

```python
"""
Parse Python source files into a dependency graph using the ast module.
Scoped to handle ONLY the patterns in the demo repo (see AGENT-SPEC.md, Ruling 7):
- Standard imports: from src.auth import validate_token
- Function definitions: def function_name(args):
- Class definitions: class ClassName:
- Direct function calls: validate_token(token)
- Method calls on imported modules: auth.validate_token(token)

Does NOT handle: star imports, dynamic imports, importlib, decorators that
modify signatures, metaclasses, __init__.py re-exports.
"""

import ast
from dataclasses import dataclass

@dataclass
class GraphNode:
    id: str          # "auth.validate_token"
    file: str        # "src/auth.py"
    type: str        # "function" | "class"
    line: int        # line number of definition
    name: str        # "validate_token" (short name)

@dataclass
class GraphEdge:
    source: str      # "users.get_user" (the caller)
    target: str      # "auth.validate_token" (the callee)
    type: str        # "calls" | "imports"

def parse_file(file_path: str, source_code: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    """
    Parse a single Python file into nodes (functions/classes) and edges (calls/imports).
    
    file_path: relative path like "src/auth.py"
    source_code: the raw file content
    
    Returns (nodes, edges) for this file.
    
    Implementation:
    1. ast.parse(source_code)
    2. Walk the tree:
       a. ast.FunctionDef / ast.AsyncFunctionDef → create GraphNode
          id = module_name + "." + function_name
          module_name = file_path without "src/" prefix and ".py" suffix
          e.g. "src/auth.py" → "auth", "src/utils/helpers.py" → "utils.helpers"
       b. ast.ClassDef → create GraphNode (type="class")
       c. ast.ImportFrom → create GraphEdge(type="imports")
          e.g. "from src.auth import validate_token"
          → edge from current module to "auth.validate_token"
       d. ast.Call → create GraphEdge(type="calls")
          Resolve the callee:
          - ast.Name(id="validate_token") → look up in imports → "auth.validate_token"
          - ast.Attribute(value=Name("auth"), attr="validate_token") → "auth.validate_token"
          Find which function this call is inside (walk up to enclosing FunctionDef)
          → edge from "current_module.enclosing_function" to resolved callee
    """
    ...

def build_graph(file_contents: dict[str, str]) -> dict:
    """
    Build the full dependency graph from multiple files.
    
    file_contents: {"src/auth.py": "source...", "src/users.py": "source...", ...}
    
    Returns {
        "nodes": [{"id": "auth.validate_token", "file": "src/auth.py", "type": "function", "line": 42}, ...],
        "edges": [{"from": "users.get_user", "to": "auth.validate_token", "type": "calls"}, ...]
    }
    
    Implementation:
    1. For each file in file_contents: call parse_file()
    2. Merge all nodes and edges
    3. Remove edges where target doesn't exist in nodes (external/stdlib imports)
    4. Return the merged graph as a dict
    """
    ...

def get_subgraph(full_graph: dict, relevant_files: list[str]) -> dict:
    """
    Extract only the subgraph reachable from nodes in relevant_files.
    Used to keep the graph small for the LLM context.
    
    BFS from all nodes in relevant_files through edges in both directions.
    Max depth: 3 hops.
    """
    ...
```

#### 1.4 Blast Radius Tracer (`backend/app/services/blast_radius.py`)

```python
"""
Trace transitive dependents of changed code through the dependency graph.
Pure graph traversal — no LLM or API calls.
"""

from collections import deque

def trace(changed_identifiers: list[str], dep_graph: dict) -> dict:
    """
    BFS from changed_identifiers through dep_graph edges (REVERSE direction:
    who calls this changed function?).
    
    changed_identifiers: ["auth.validate_token", "auth.reset_password"]
    dep_graph: {"nodes": [...], "edges": [...]}  (from ast_parser.build_graph)
    
    Returns {
        "directly_changed": ["auth.validate_token", "auth.reset_password"],
        "depth_1_impacted": ["users.get_user", "users.update_profile", "orders.create_order"],
        "depth_2_impacted": ["billing.charge", "admin.dashboard"],
        "depth_3_impacted": [...],
        "highest_risk_path": "auth.validate_token → users.get_user → orders.create_order → billing.charge",
        "risk_score": 0.65,
        "untested_impacted": ["notifications.send_email", "billing.charge"],
        "impacted_details": [
            {"name": "users.get_user", "depth": 1, "file": "src/users.py", "has_test": true},
            ...
        ]
    }
    
    RISK SCORE CALCULATION:
    1. impacted_ratio = num_impacted / total_nodes_in_graph
    2. depth_weight = weighted sum: depth_1 nodes × 1.0, depth_2 × 0.7, depth_3+ × 0.4
    3. untested_penalty = 1.0 + (0.1 × num_untested_impacted)
    4. risk_score = min(1.0, impacted_ratio × depth_weight × untested_penalty)
    
    TEST FILE DETECTION:
    For each impacted node in file "src/X.py":
    - Look for "tests/test_X.py" in the graph's file list
    - If not found → add to untested_impacted
    
    HIGHEST RISK PATH:
    The longest path from any changed identifier to a leaf node, preferring
    untested nodes. Format: "A → B → C → D"
    """
    
    # Build reverse adjacency list
    # (for each node, who calls it?)
    reverse_adj = {}
    for edge in dep_graph.get("edges", []):
        target = edge["to"] if "to" in edge else edge.get("target")
        source = edge["from"] if "from" in edge else edge.get("source")
        if target not in reverse_adj:
            reverse_adj[target] = []
        reverse_adj[target].append(source)
    
    # BFS from changed identifiers through reverse edges
    visited = {}  # node_id → depth
    queue = deque()
    
    for ci in changed_identifiers:
        visited[ci] = 0
        queue.append((ci, 0))
    
    while queue:
        current, depth = queue.popleft()
        for caller in reverse_adj.get(current, []):
            if caller not in visited:
                visited[caller] = depth + 1
                queue.append((caller, depth + 1))
    
    # Group by depth
    # ... (implement grouping, test detection, risk scoring, path finding)
    ...
```

#### 1.5 Cache Service (`backend/app/services/cache.py`)

If this already exists, verify it has `get` and `set` methods. If not:

```python
"""
SQLite-backed async cache.
Keys: "depgraph:{repo}:{sha}:{path_filter}", "issue:{repo}:{number}:{updated_at}"
"""

import json
from datetime import datetime, timezone
from app.database import get_db

async def cache_get(key: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT value, expires_at FROM cache WHERE cache_key = ?", (key,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    if row[1] and row[1] < datetime.now(timezone.utc).isoformat():
        await db.execute("DELETE FROM cache WHERE cache_key = ?", (key,))
        await db.commit()
        return None
    return json.loads(row[0])

async def cache_set(key: str, value: dict, expires_at: str = None):
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR REPLACE INTO cache (cache_key, value, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (key, json.dumps(value), now, expires_at)
    )
    await db.commit()
```

### VERIFY

```bash
# 1. GitHub client — fetch a real PR diff
python -c "
import asyncio
from app.services.github_client import GitHubClient
async def test():
    gh = GitHubClient()
    # Use any public repo with a PR — or the demo repo if it exists
    diff = await gh.get_pr_diff('octocat', 'Hello-World', 1)
    print(f'Diff length: {len(diff)} chars')
    assert len(diff) > 0, 'Empty diff'
    print('GitHub client OK')
asyncio.run(test())
"

# 2. Diff parser — parse a sample diff
python -c "
from app.services.diff_parser import parse_diff, extract_changed_identifiers
sample = '''diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -40,6 +40,12 @@ def generate_reset_token(user_id):
+def reset_password(email):
+    token = generate_reset_token(email)
+    return token
'''
result = parse_diff(sample)
print(f'Files: {len(result[\"files\"])}')
assert len(result['files']) > 0, 'No files parsed'
print('Diff parser OK')
"

# 3. AST parser — parse demo repo files
python -c "
from app.services.ast_parser import parse_file, build_graph
# Use a minimal test file
source = '''
from src.auth import validate_token

def get_user(user_id, token):
    validate_token(token)
    return {\"id\": user_id}
'''
nodes, edges = parse_file('src/users.py', source)
print(f'Nodes: {len(nodes)}, Edges: {len(edges)}')
assert len(nodes) >= 1, 'No nodes found'
assert len(edges) >= 1, 'No edges found'
print('AST parser OK')
"

# 4. Blast radius — trace on a known graph
python -c "
from app.services.blast_radius import trace
graph = {
    'nodes': [
        {'id': 'auth.validate_token', 'file': 'src/auth.py', 'type': 'function', 'line': 10},
        {'id': 'users.get_user', 'file': 'src/users.py', 'type': 'function', 'line': 5},
        {'id': 'orders.create_order', 'file': 'src/orders.py', 'type': 'function', 'line': 8},
    ],
    'edges': [
        {'from': 'users.get_user', 'to': 'auth.validate_token', 'type': 'calls'},
        {'from': 'orders.create_order', 'to': 'users.get_user', 'type': 'calls'},
    ]
}
result = trace(['auth.validate_token'], graph)
print(f'Depth 1: {result[\"depth_1_impacted\"]}')
print(f'Depth 2: {result[\"depth_2_impacted\"]}')
print(f'Risk: {result[\"risk_score\"]}')
assert 'users.get_user' in result['depth_1_impacted'], 'Missing depth 1'
assert 'orders.create_order' in result['depth_2_impacted'], 'Missing depth 2'
assert result['risk_score'] > 0, 'Risk score is 0'
print('Blast radius OK')
"

# 5. Cache — set and get
python -c "
import asyncio
from app.services.cache import cache_get, cache_set
async def test():
    await cache_set('test:key', {'hello': 'world'})
    result = await cache_get('test:key')
    assert result == {'hello': 'world'}, f'Cache mismatch: {result}'
    print('Cache OK')
asyncio.run(test())
"
```

### EXIT GATE

All 5 verify checks pass. GitHub client fetches real data, diff parser extracts files, AST parser finds nodes and edges, blast radius traces correctly, cache round-trips.

---

## Phase 2: Tool Registry + Agent Orchestrator

**Purpose:** Wire the services from Phase 1 into callable tools and build the phased ReAct loop that calls DeepSeek for reasoning and Jev for decisions.

### BUILD

#### 2.1 Tool Registry (`backend/app/agent/tools/registry.py`)

Implement exactly as specified in AGENT-SPEC.md. The registry holds tool definitions with:
- `name`: string identifier the LLM uses to call it
- `description`: string the LLM sees in its system prompt
- `parameters`: dict of param names → types
- `fn`: the async callable

Register all 6 tools:
- `fetch_pr_diff` → calls `github_client.get_pr_diff` + `diff_parser.parse_diff`
- `fetch_linked_issue` → calls `github_client.get_linked_issue`
- `build_dependency_graph` → calls `cache.cache_get` first, then `github_client.get_file_tree` + `github_client.get_file_content` per file + `ast_parser.build_graph`, then `cache.cache_set`
- `trace_blast_radius` → calls `blast_radius.trace`
- `fetch_file_content` → calls `github_client.get_file_content`
- `post_pr_review` → calls `github_client.post_comment`

Each tool function must:
1. Accept the parameters defined in AGENT-SPEC.md
2. Have access to the repo owner/name (passed via closure or class attribute)
3. Return a JSON-serializable result
4. Handle errors gracefully (return error string, don't throw)

#### 2.2 Output Parser (`backend/app/agent/parser.py`)

Implement exactly as specified in AGENT-SPEC.md. Must handle:
- `Thought: ...` → ParsedOutput(type="thought")
- `Action: tool_name(args)` → ParsedOutput(type="action", tool_name=..., tool_args=...)
- `Answer: {...}` → ParsedOutput(type="answer")
- Raw JSON starting with `{` → ParsedOutput(type="answer") — the Bug 3 fix
- Markdown code fence stripping before parsing
- Fallback: anything unrecognized → treat as thought

#### 2.3 System Prompts (`backend/app/agent/prompts.py`)

Copy the DIAGNOSE_PROMPT and VERIFY_PROMPT exactly from AGENT-SPEC.md. The TRIAGE_PROMPT is no longer used (replaced by Jev) but keep it as a comment for legacy fallback.

#### 2.4 Composer Prompt Generator (`backend/app/agent/composer_prompt.py`)

Implement the template-based generator from AGENT-SPEC.md. No LLM call — pure string formatting. Takes diagnosis dict + triage dict, returns a markdown string.

#### 2.5 Agent Orchestrator (`backend/app/agent/orchestrator.py`)

This is the core. Implement the phased loop:

```python
class AgentOrchestrator:
    def __init__(self, deepseek: LLMClient, jev: JevClient, tools: ToolRegistry, sse: SSEManager):
        self.deepseek = deepseek
        self.jev = jev
        self.tools = tools
        self.sse = sse

    async def run(self, task_id: str, owner: str, repo: str, pr_number: int, head_sha: str, mode: str = "full"):
        """
        Main entry point. Called by the webhook handler.
        
        mode="full": new PR → DIAGNOSE → JEV_FAST_EXIT_CHECK → TRIAGE (Jev) → DISPATCH
        mode="verify": follow-up push → VERIFY (Jev)
        """
        if mode == "full":
            # Phase 1: DIAGNOSE (DeepSeek ReAct loop)
            diagnosis = await self._run_diagnose(task_id, owner, repo, pr_number, head_sha)
            
            # Check: if trivial, skip triage
            if diagnosis.get("is_trivial"):
                triage = {"severity": "TRIVIAL", "action": "skip", "justification": "Trivial PR", ...}
            else:
                # Phase 2: TRIAGE (Jev — single parallel call)
                triage = await self._run_jev_triage(task_id, diagnosis)
            
            # Phase 3: DISPATCH (no LLM — template + persist + post)
            await self._run_dispatch(task_id, owner, repo, pr_number, head_sha, diagnosis, triage)
            
        elif mode == "verify":
            await self._run_jev_verify(task_id, owner, repo, pr_number, head_sha)

    async def _run_diagnose(self, task_id, owner, repo, pr_number, head_sha) -> dict:
        """
        ReAct loop using DeepSeek V4 Flash.
        Max 8 iterations. Tools: all 6 from registry.
        
        STEP 1: Always fetch diff first
        STEP 2: Jev fast-exit check — is this trivial?
                If trivial (noul > 0.9), return early with is_trivial=true
        STEP 3: Fetch linked issue
        STEP 4: If diff touches functions → build dep graph
        STEP 5: Trace blast radius
        STEP 6: Compile diagnosis JSON
        
        At each step, emit SSE events for the dashboard.
        Persist each step to trace_steps table.
        """
        ...

    async def _run_jev_triage(self, task_id, diagnosis) -> dict:
        """
        ONE Jev system_one() call with all classification questions.
        See JEV-INTEGRATION-UPDATE.md for the full question set.
        
        Then ONE short DeepSeek call for the narrative (suggested_fix_approach).
        
        Emit SSE events for triage phase.
        """
        ...

    async def _run_dispatch(self, task_id, owner, repo, pr_number, head_sha, diagnosis, triage):
        """
        NO LLM calls. Pure application logic.
        
        1. Generate Composer prompt (template)
        2. Generate review markdown
        3. Persist task to DB (using the task_id from webhook — NOT a new UUID)
        4. Post GitHub comment → get comment_id
        5. Update task status to 'dispatched' (Bug 5 fix)
        6. If action=dispatch_urgent → create GitHub issue
        7. Emit SSE dispatch complete event
        """
        ...

    async def _run_jev_verify(self, task_id, owner, repo, pr_number, head_sha):
        """
        ONE Jev call checking each previously-missing requirement.
        See JEV-INTEGRATION-UPDATE.md for the full implementation.
        
        task_id is the EXISTING task's id (Bug 4 fix).
        Updates the existing row, never creates a new one.
        """
        ...
```

#### 2.6 SSE Manager (`backend/app/services/sse_manager.py`)

```python
"""
In-memory event broadcaster. One event queue per task_id.
Dashboard subscribes via GET /api/stream/{task_id}.
Agent emits events at every step.
"""

import asyncio
import json
from collections import defaultdict

class SSEManager:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def emit(self, task_id: str, event: dict):
        """Push event to all subscribers of this task_id."""
        for queue in self._subscribers.get(task_id, []):
            queue.put_nowait(event)

    async def subscribe(self, task_id: str):
        """Async generator that yields events for a task_id."""
        queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("type") == "answer" and event.get("phase") == "dispatch":
                    break  # Final event
                if event.get("type") == "error":
                    break
        finally:
            self._subscribers[task_id].remove(queue)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

# Singleton
sse_manager = SSEManager()
```

### VERIFY

```bash
# 1. Tool registry — all 6 tools registered
python -c "
from app.agent.tools.registry import ToolRegistry
# ... register all tools ...
reg = ToolRegistry()
# (registration happens here or in a setup function)
assert reg.has('fetch_pr_diff'), 'Missing fetch_pr_diff'
assert reg.has('fetch_linked_issue'), 'Missing fetch_linked_issue'
assert reg.has('build_dependency_graph'), 'Missing build_dependency_graph'
assert reg.has('trace_blast_radius'), 'Missing trace_blast_radius'
assert reg.has('fetch_file_content'), 'Missing fetch_file_content'
assert reg.has('post_pr_review'), 'Missing post_pr_review'
print(f'6 tools registered. Prompt:\\n{reg.get_schemas_for_prompt()[:200]}...')
print('Tool registry OK')
"

# 2. Parser — handles all output types
python -c "
from app.agent.parser import parse_agent_output
t = parse_agent_output('Thought: I need the diff')
assert t.type == 'thought', f'Expected thought, got {t.type}'

a = parse_agent_output('Action: fetch_pr_diff({\"pr_number\": 42})')
assert a.type == 'action' and a.tool_name == 'fetch_pr_diff', f'Bad action parse: {a}'

ans = parse_agent_output('Answer: {\"severity\": \"HIGH\"}')
assert ans.type == 'answer', f'Expected answer, got {ans.type}'

raw = parse_agent_output('{\"severity\": \"HIGH\"}')
assert raw.type == 'answer', f'Raw JSON should be answer, got {raw.type}'

print('Parser OK')
"

# 3. Composer prompt — generates from sample data
python -c "
from app.agent.composer_prompt import generate_composer_prompt
diagnosis = {
    'linked_issue': {'number': 12, 'requirements': ['Validate email', 'Send email']},
    'intent_alignment': {'addressed': ['Send email'], 'missing': ['Validate email'], 'scope_creep': []},
    'blast_radius': {'highest_risk_path': 'auth → users → orders', 'risk_score': 0.65, 'untested_impacted': ['billing']},
    'changed_files': ['src/auth.py'],
}
triage = {
    'severity': 'HIGH',
    'action': 'dispatch',
    'suggested_fix_approach': 'Add email validation before generating token.',
    'affected_files_priority': [{'path': 'src/auth.py', 'lines': [42], 'change_type': 'modified'}],
}
prompt = generate_composer_prompt(diagnosis, triage)
assert 'Validate email' in prompt, 'Missing requirement in prompt'
assert 'src/auth.py' in prompt, 'Missing file in prompt'
print(f'Prompt length: {len(prompt)} chars')
print('Composer prompt OK')
"

# 4. SSE manager — emit and receive
python -c "
import asyncio
from app.services.sse_manager import SSEManager
async def test():
    mgr = SSEManager()
    received = []
    async def consumer():
        async for event in mgr.subscribe('test-task'):
            received.append(event)
    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)
    mgr.emit('test-task', {'step': 0, 'type': 'thought', 'phase': 'diagnose', 'content': 'hello'})
    mgr.emit('test-task', {'step': 1, 'type': 'answer', 'phase': 'dispatch', 'content': 'done'})
    await asyncio.sleep(0.1)
    await task
    assert len(received) == 2, f'Expected 2 events, got {len(received)}'
    print('SSE manager OK')
asyncio.run(test())
"
```

### EXIT GATE

All 4 verify checks pass. Tools registered, parser handles all formats, Composer prompt generates correctly, SSE broadcasts and receives.

---

## Phase 3: API Routes + Webhook Integration

**Purpose:** Wire the agent into the FastAPI routes so a real GitHub webhook triggers the full pipeline.

### BUILD

#### 3.1 Webhook Route (`backend/app/routes/webhook.py`)

If this exists, update it. If not, create it per API-SPEC.md:

```python
"""
POST /api/webhook/github
- Verify HMAC signature
- Filter for pull_request opened/synchronize
- Mint task_id (single source of truth — Bug 2 fix)
- If synchronize + existing task → reuse existing task_id, mode=verify (Bug 4 fix)
- Dispatch agent async
- Return 202 with task_id
"""
```

Key points from PATCH-REPORT.md:
- `task_id` is minted HERE and passed to the agent. `create_task()` does NOT mint its own.
- For `synchronize` on a PR with an existing task, reuse that task's `id`.

#### 3.2 SSE Stream Route (`backend/app/routes/stream.py`)

```python
"""
GET /api/stream/{task_id}
Returns SSE text/event-stream.
Uses sse-starlette's EventSourceResponse.
"""
```

#### 3.3 Tasks Route (`backend/app/routes/tasks.py`)

```python
"""
GET  /api/tasks?repo={owner/name}&status={csv}  — Extension polling
GET  /api/tasks/{task_id}                        — Task detail
PATCH /api/tasks/{task_id}                       — Extension status update
"""
```

Implement status transition validation per DATABASE.md:
- `pending → dispatched` (agent only)
- `dispatched → accepted | dismissed` (extension)
- `accepted → in_progress` (extension)
- `in_progress → resolved | dispatched` (verify phase)
- `resolved, error, dismissed` are terminal — reject transitions from these

#### 3.4 Reviews Route (`backend/app/routes/reviews.py`)

```python
"""
GET /api/reviews?repo={owner/name}&limit=20&offset=0  — Dashboard list
GET /api/reviews/{task_id}                              — Full detail with trace + blast radius
"""

# The detail endpoint returns EVERYTHING:
# task fields + trace steps + parsed blast_radius_json + parsed diagnosis_json
# + jev_confidences + requirement_scores + composer_prompt
```

#### 3.5 Register Routes in `main.py`

```python
from app.routes import webhook, stream, tasks, reviews, health

app.include_router(webhook.router)
app.include_router(stream.router)
app.include_router(tasks.router)
app.include_router(reviews.router)
app.include_router(health.router)
```

Also set up CORS:
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

And create the agent + sse_manager as app lifespan singletons.

### VERIFY

```bash
# 1. All routes registered
curl -s http://localhost:8000/openapi.json | python -c "
import json, sys
spec = json.load(sys.stdin)
paths = list(spec['paths'].keys())
print(f'Routes: {paths}')
required = ['/api/webhook/github', '/api/stream/{task_id}', '/api/tasks', '/api/tasks/{task_id}', '/api/reviews', '/api/reviews/{task_id}', '/api/health']
for r in required:
    assert r in paths, f'Missing route: {r}'
print('All routes registered OK')
"

# 2. Webhook rejects bad signature
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=invalid" \
  -d '{"action":"opened"}'
# EXPECT: 401

# 3. Tasks API returns empty list
curl -s "http://localhost:8000/api/tasks?repo=test/repo&status=pending" | python -m json.tool
# EXPECT: {"tasks": []}

# 4. Reviews API returns empty list
curl -s "http://localhost:8000/api/reviews?repo=test/repo" | python -m json.tool
# EXPECT: {"reviews": [], "total": 0}

# 5. SSE endpoint connects (will hang waiting for events — that's correct)
timeout 3 curl -s -N http://localhost:8000/api/stream/test-id || true
# EXPECT: Connection established then timeout (no events)

# 6. Health includes all fields
curl -s http://localhost:8000/api/health | python -c "
import json, sys
h = json.load(sys.stdin)
assert 'status' in h
assert 'reviews_completed' in h
assert 'llm_provider' in h or 'uptime_seconds' in h
print('Health OK')
"
```

### EXIT GATE

All 6 checks pass. Routes registered, webhook rejects bad signatures, APIs return correct empty-state responses, SSE connects.

---

## Phase 4: End-to-End Agent Test (CLI)

**Purpose:** Run the full agent pipeline on a sample PR WITHOUT the webhook. This isolates agent logic from the HTTP layer.

### BUILD

#### 4.1 CLI Test Script (`backend/test_agent_e2e.py`)

```python
"""
End-to-end agent test. Run manually:
    python test_agent_e2e.py <owner> <repo> <pr_number>
    
Example:
    python test_agent_e2e.py amartya-kumar pr-sentinel-demo 1

This script:
1. Creates a task_id
2. Runs the agent orchestrator directly (no webhook)
3. Prints the diagnosis, triage, and composer prompt
4. Shows all SSE events that would have been emitted
5. Shows the task record from the DB
"""

import asyncio
import sys
import json

async def main():
    owner, repo, pr_number = sys.argv[1], sys.argv[2], int(sys.argv[3])
    
    # Import and initialize
    from app.services.llm_client import LLMClient
    from app.services.jev_client import JevClient
    from app.agent.tools.registry import create_tool_registry  # factory that registers all 6 tools
    from app.services.sse_manager import SSEManager
    from app.agent.orchestrator import AgentOrchestrator
    from app.database import get_db
    import uuid
    
    deepseek = LLMClient()
    jev = JevClient()
    sse = SSEManager()
    tools = create_tool_registry(owner, repo)
    agent = AgentOrchestrator(deepseek, jev, tools, sse)
    
    task_id = str(uuid.uuid4())
    print(f"Task ID: {task_id}")
    print(f"Analyzing PR #{pr_number} on {owner}/{repo}...")
    print("=" * 60)
    
    # Collect SSE events
    events = []
    async def collector():
        async for event in sse.subscribe(task_id):
            events.append(event)
            phase = event.get('phase', '?')
            etype = event.get('type', '?')
            content = str(event.get('content', ''))[:100]
            tool = event.get('tool', '')
            print(f"  [{phase}] {etype}: {tool + ' ' if tool else ''}{content}")
    
    collector_task = asyncio.create_task(collector())
    
    # Get head SHA
    from app.services.github_client import GitHubClient
    gh = GitHubClient()
    pr_info = await gh.get_pr_info(owner, repo, pr_number)
    head_sha = pr_info["head_sha"]
    
    # Run agent
    await agent.run(task_id, owner, repo, pr_number, head_sha, mode="full")
    
    await asyncio.sleep(0.5)
    collector_task.cancel()
    
    print("=" * 60)
    print(f"Total SSE events: {len(events)}")
    
    # Read task from DB
    db = await get_db()
    cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cursor.fetchone()
    if row:
        task = dict(row)
        print(f"\nSeverity: {task['severity']}")
        print(f"Action: {task['action']}")
        print(f"Status: {task['status']}")
        if task.get('jev_confidences'):
            print(f"Jev confidences: {task['jev_confidences']}")
        if task.get('requirement_scores'):
            print(f"Requirement scores: {task['requirement_scores']}")
        print(f"\nComposer prompt:\n{task.get('composer_prompt', 'N/A')[:500]}")
    else:
        print("ERROR: No task found in DB!")

if __name__ == "__main__":
    asyncio.run(main())
```

### VERIFY

This phase requires the demo repo to exist on GitHub. If it doesn't exist yet, create it first (see Phase 6). If it does:

```bash
# Run against Scenario 1 (missing requirements + high blast radius)
python test_agent_e2e.py amartya-kumar pr-sentinel-demo 1

# EXPECTED OUTPUT:
# - DIAGNOSE phase: 4-5 SSE events (thought → action → observation cycles)
# - Jev fast-exit: is_trivial should be LOW (< 0.2)
# - TRIAGE: severity should be CRITICAL or HIGH
# - DISPATCH: task persisted, status = dispatched
# - Composer prompt contains "Validate email" and "Expire token"
# - Jev confidences show per-requirement scores
# - GitHub comment posted (check the PR on GitHub)
```

### EXIT GATE

The CLI test produces a correct diagnosis with:
- At least 1 missing requirement identified
- Severity is CRITICAL or HIGH
- Task is in the database with status `dispatched`
- Composer prompt mentions the missing requirements
- Jev confidence scores are populated
- GitHub comment is visible on the PR

---

## Phase 5: Webhook-Triggered End-to-End

**Purpose:** Verify the full pipeline works when triggered by a real GitHub webhook, not just CLI.

### BUILD

Nothing new to build — this phase is pure integration testing.

### VERIFY

```bash
# 1. Start the server
uvicorn app.main:app --port 8000

# 2. Start ngrok tunnel
ngrok http 8000
# Note the https URL

# 3. Set the ngrok URL as the webhook on the demo repo
# GitHub → demo-repo → Settings → Webhooks → Edit
# URL: https://xxxx.ngrok-free.app/api/webhook/github

# 4. Open a PR on the demo repo (Scenario 1 branch)
# git checkout feature/1-password-reset
# git push origin feature/1-password-reset
# Open PR on GitHub

# 5. Watch the server logs — should show:
# - Webhook received
# - HMAC verified
# - Agent started
# - DIAGNOSE phase tool calls
# - Jev triage call
# - Task persisted
# - GitHub comment posted

# 6. Check the PR on GitHub — review comment should be visible

# 7. Check the task API
curl -s "http://localhost:8000/api/tasks?repo=amartya-kumar/pr-sentinel-demo&status=dispatched"
# EXPECT: task with severity=CRITICAL, composer_prompt populated

# 8. Check the SSE stream
# (open a new terminal before pushing the PR)
curl -N http://localhost:8000/api/stream/{task_id}
# EXPECT: stream of thought/action/observation/answer events
```

### EXIT GATE

A real GitHub webhook triggers the full pipeline: webhook → agent → triage → dispatch → GitHub comment. Task visible in API. SSE events stream correctly.

---

## Phase 6: Demo Repository

**Purpose:** Create the separate demo repo that PR Sentinel monitors.

### BUILD

Create a NEW GitHub repository called `pr-sentinel-demo`. Follow DEMO-REPO.md exactly for:
- 6 source files (`src/auth.py`, `users.py`, `orders.py`, `notifications.py`, `billing.py`, `admin.py`)
- 3 test files (`tests/test_auth.py`, `test_users.py`, `test_orders.py`)
- Intentionally NO `test_notifications.py` or `test_billing.py`
- 4 GitHub issues
- 4 branches (one per scenario)
- Webhook pointing to the PR Sentinel backend

### VERIFY

```bash
# Run the CLI test against each scenario
python test_agent_e2e.py amartya-kumar pr-sentinel-demo 1
# EXPECT: CRITICAL, 2 missing requirements

python test_agent_e2e.py amartya-kumar pr-sentinel-demo 2
# EXPECT: MEDIUM, scope creep flagged

python test_agent_e2e.py amartya-kumar pr-sentinel-demo 3
# EXPECT: MEDIUM, phantom PR

python test_agent_e2e.py amartya-kumar pr-sentinel-demo 4
# EXPECT: TRIVIAL, skipped
```

### EXIT GATE

All 4 scenarios produce the expected severity and flags.

---

## Phase 7: Dashboard Frontend

**Purpose:** Build the React dashboard with live agent trace, blast radius graph, and intent report.

### BUILD

Follow DASHBOARD-SPEC.md exactly. Build in this order:

1. **Scaffold:** Vite + React + Tailwind + React Router (2 routes: `/` and `/review/:taskId`)
2. **API client** (`src/api/client.js`): axios instance, getReviews, getReviewDetail, getHealth
3. **Layout.jsx**: sidebar with nav + main content area
4. **Dashboard page** (`/`): PRList.jsx with RiskBadge.jsx — fetch `/api/reviews`, render list
5. **ReviewDetail page** (`/review/:taskId`):
   - AgentTrace.jsx + TraceStep.jsx (SSE-powered, see DASHBOARD-SPEC.md for useSSE hook)
   - IntentReport.jsx (addressed/missing/scope creep)
   - BlastRadiusGraph.jsx (React Flow with dagre layout, depth-colored nodes, animated propagation)
   - ComposerPromptView.jsx (monospace text + copy button)
   - TaskLifecycle.jsx (status pipeline: pending → dispatched → accepted → in_progress → resolved)
6. **Requirement confidence heatmap**: new component showing Jev's per-requirement Noul scores (green > 0.6, red < 0.4, amber between)

### VERIFY

```bash
# 1. Dashboard loads
cd dashboard && npm run dev
# Open http://localhost:5173
# EXPECT: Dashboard loads with sidebar, empty review list

# 2. Review list populates (requires tasks from Phase 4/5)
# EXPECT: Past reviews appear with severity badges

# 3. Click a review → detail page loads
# EXPECT: Agent trace shows, blast radius graph renders, intent report shows

# 4. Live SSE test: trigger a new PR while dashboard is open
# EXPECT: Agent trace streams in real-time with auto-scroll

# 5. Blast radius graph animates (nodes light up by depth)
# EXPECT: Changed nodes (red) → depth 1 (orange) → depth 2 (yellow) animate on load

# 6. Composer prompt has copy button that works
# EXPECT: Click "Copy" → clipboard has the prompt text

# 7. Requirement scores show per-requirement confidence
# EXPECT: Each requirement has a colored bar/value showing Jev's Noul score
```

### EXIT GATE

Dashboard renders all components. SSE trace streams live. Blast radius graph animates. All data from the API displays correctly.

---

## Phase 8: VS Code / Cursor Extension

**Purpose:** Build the extension that delivers tasks to the IDE.

### BUILD

Follow EXTENSION-SPEC.md exactly. Build in this order:

1. **Scaffold:** `yo code` TypeScript extension, webpack config, package.json manifest
2. **git.ts**: read repo owner/name from git remote
3. **sentinelClient.ts**: HTTP client → `GET /api/tasks`, `PATCH /api/tasks/{id}`
4. **taskPoller.ts**: poll every 30s, detect new tasks, call taskNotifier
5. **taskNotifier.ts**: `vscode.window.showInformationMessage` with [Accept] [Dismiss] [Dashboard] buttons
6. **fileNavigator.ts**: on Accept → open files at correct lines
7. **contextInjector.ts**: write `.cursor/rules/sentinel-context.mdc` with Composer prompt + diagnosis
8. **statusReporter.ts**: watch file saves → PATCH status to `in_progress`
9. **blastRadiusPanel.ts**: webview sidebar showing simplified blast radius + intent report
10. **extension.ts**: wire everything together, register commands

### VERIFY

```bash
# 1. Extension compiles
cd extension && npm run compile
# EXPECT: No TypeScript errors

# 2. Package as VSIX
npx @vscode/vsce package
# EXPECT: pr-sentinel-0.1.0.vsix created

# 3. Install in Cursor
cursor --install-extension pr-sentinel-0.1.0.vsix
# EXPECT: Extension appears in Extensions panel

# 4. Open the demo repo in Cursor with the extension active
# Set prSentinel.backendUrl to your running backend
# EXPECT: Status bar shows "Sentinel" icon

# 5. Trigger a PR (or manually create a task via CLI)
# EXPECT: Notification appears within 30 seconds

# 6. Click Accept
# EXPECT: Files open at correct lines
# EXPECT: .cursor/rules/sentinel-context.mdc exists with diagnosis

# 7. Open Cursor Composer → type @
# EXPECT: sentinel-context appears as available context

# 8. Save an affected file
# EXPECT: Task status updates to in_progress (check via API)
```

### EXIT GATE

Extension installs in Cursor, polls tasks, shows notifications, opens files, injects `.cursor/rules/`, and reports status back to the backend.

---

## Phase 9: VERIFY Phase (Loop Closure)

**Purpose:** When a developer pushes a fix commit, PR Sentinel re-analyzes and closes the loop.

### BUILD

The VERIFY phase logic is already in the orchestrator (Phase 2). This phase verifies it works end-to-end.

1. Ensure the webhook handler detects `synchronize` events on PRs with existing tasks
2. Ensure it reuses the existing task_id (Bug 4 fix)
3. Ensure the Jev verify call checks each previously-missing requirement
4. Ensure the task status updates to `resolved` when all fixed
5. Ensure a "✅ All issues addressed" comment posts on the PR

### VERIFY

```bash
# 1. Start with Scenario 1 PR already analyzed (from Phase 5)
# The task should be in status=dispatched

# 2. Fix the code on the PR branch
# Add email validation + token expiry to reset_password()
# Push the fix commit

# 3. Webhook fires with action=synchronize
# EXPECT: Server logs show "mode=verify, reusing task_id=..."

# 4. Jev verify runs
# EXPECT: requirement_scores update — both requirements now > 0.6

# 5. Task status becomes resolved
curl -s "http://localhost:8000/api/tasks/{task_id}" | python -c "
import json, sys
t = json.load(sys.stdin)
print(f'Status: {t[\"status\"]}')
assert t['status'] == 'resolved', f'Expected resolved, got {t[\"status\"]}'
print('Verify phase OK')
"

# 6. GitHub PR has a new comment: "✅ All issues addressed"
# Check the PR on GitHub

# 7. Dashboard shows the task lifecycle: pending → dispatched → resolved
```

### EXIT GATE

Fix push triggers re-verification. Task transitions to `resolved`. GitHub comment confirms. Dashboard shows complete lifecycle.

---

## Phase 10: Deployment

**Purpose:** Deploy backend to Railway, dashboard to Vercel.

### BUILD

#### 10.1 Backend → Railway

```bash
cd backend
# railway.toml already exists from specs
railway login
railway init  # or link to existing project
railway volume add  # for SQLite persistence
railway up

# Set env vars in Railway dashboard:
# GITHUB_WEBHOOK_SECRET, GITHUB_TOKEN, DEEPSEEK_API_KEY,
# TYPESAFE_API_KEY, JEV_MODEL, LLM_MODEL, DEEPSEEK_BASE_URL,
# FRONTEND_URL, ALLOWED_ORIGINS, DATABASE_PATH

# Note the Railway URL
```

#### 10.2 Dashboard → Vercel

```bash
cd dashboard
# Create vercel.json with API proxy rewrites (see DASHBOARD-SPEC.md)
npx vercel --prod

# Update FRONTEND_URL and ALLOWED_ORIGINS in Railway to include the Vercel URL
```

#### 10.3 Update GitHub Webhook

Change the demo repo's webhook URL from ngrok to the Railway production URL.

### VERIFY

```bash
# 1. Backend health on production
curl -s https://YOUR-APP.up.railway.app/api/health | python -m json.tool
# EXPECT: {"status": "ok", ...}

# 2. Dashboard loads from Vercel
# Open https://pr-sentinel.vercel.app (or your Vercel URL)
# EXPECT: Dashboard loads, shows reviews

# 3. Full pipeline on production
# Open a PR on demo repo
# EXPECT: Webhook hits Railway → agent runs → comment posted → dashboard updates

# 4. Extension works against production
# Update prSentinel.backendUrl to Railway URL
# EXPECT: Extension polls and receives tasks from production
```

### EXIT GATE

Backend on Railway, dashboard on Vercel, webhook on production URL, extension connects to production. Full pipeline works on deployed infrastructure.

---

## Phase 11: CI/CD + Dogfooding

**Purpose:** Add GitHub Actions CI and make PR Sentinel review its own PRs.

### BUILD

#### 11.1 GitHub Actions CI (`.github/workflows/ci.yml`)

```yaml
# See API-SPEC.md for the full CI config
# Jobs: backend-test, dashboard-build, extension-compile, self-review
```

#### 11.2 Unit Tests (`backend/tests/`)

Write tests using the fixture files in `backend/tests/fixtures/`:
- `test_diff_parser.py`: parse sample diffs, extract identifiers
- `test_ast_parser.py`: parse sample Python files, verify graph
- `test_blast_radius.py`: trace on known graph, verify impacted nodes
- `test_webhook.py`: HMAC verification, payload filtering
- `test_tasks_api.py`: CRUD operations, status transitions
- `test_agent.py`: agent loop with mocked LLM (scripted responses)

All tests mock the LLM — no live API calls in CI.

#### 11.3 README.md

Write the project README with:
- Project description (one paragraph)
- Architecture diagram (ASCII or link to dashboard)
- Setup instructions (local + production)
- Demo instructions
- Badge: "Reviewed by PR Sentinel"

### VERIFY

```bash
# 1. Tests pass locally
cd backend && pytest tests/ -v
# EXPECT: All tests pass

# 2. CI runs on push
git push origin main
# EXPECT: GitHub Actions runs, all jobs green

# 3. Self-review (dogfooding)
# Open a PR on the PR Sentinel repo itself
# EXPECT: PR Sentinel reviews its own PR via the CI trigger
```

### EXIT GATE

CI green. Tests pass. Self-review works.

---

## Phase 12: Polish + Presentation

**Purpose:** Final polish and demo preparation.

### BUILD

1. **Error states in dashboard**: loading spinners, error messages, empty states
2. **Extension error handling**: backend offline indicator, retry logic
3. **Edge cases**: large diffs (>50 files), trivial PRs, vague issues, AST parse failures
4. **Demo rehearsal**: run all 4 scenarios end-to-end 3 times
5. **Backup demo video**: screen-record one complete run in case live demo fails
6. **Presentation slides**: problem, architecture, live demo, Agentic AI deep dive, DevOps deep dive, future work

### VERIFY

```bash
# Run all 4 demo scenarios on production
# Scenario 1: CRITICAL, missing requirements
# Scenario 2: MEDIUM, scope creep
# Scenario 3: MEDIUM, phantom PR
# Scenario 4: TRIVIAL, skipped

# Each scenario must:
# 1. Complete in < 30 seconds
# 2. Produce correct severity
# 3. Stream reasoning trace on dashboard
# 4. Post GitHub comment
# 5. Deliver task to extension
# 6. Generate correct Composer prompt

# Demo rehearsal: complete the full 5-minute demo script 3 times
# (see PRD §26 for the demo script)
```

### EXIT GATE

All scenarios pass on production. Demo rehearsed 3 times. Presentation ready.

---

## Phase Summary

| Phase | What | Depends On | Estimated Time |
|---|---|---|---|
| 0 | Verify current state | Nothing | 15 min |
| 1 | Core services (GitHub, diff, AST, blast radius) | Phase 0 | 1-2 days |
| 2 | Tool registry + agent orchestrator | Phase 1 | 1-2 days |
| 3 | API routes + webhook integration | Phase 2 | 1 day |
| 4 | End-to-end agent test (CLI) | Phase 3 | 0.5 day |
| 5 | Webhook-triggered E2E | Phase 4 + ngrok | 0.5 day |
| 6 | Demo repository | Phase 4 | 0.5 day |
| 7 | Dashboard frontend | Phase 3 | 2-3 days |
| 8 | VS Code/Cursor extension | Phase 3 | 2-3 days |
| 9 | VERIFY phase (loop closure) | Phase 5 + 6 | 0.5 day |
| 10 | Deployment (Railway + Vercel) | Phase 7 + 8 | 1 day |
| 11 | CI/CD + dogfooding | Phase 10 | 1 day |
| 12 | Polish + presentation | Phase 11 | 1-2 days |

**Total: ~12-16 working days within the 3-week window.**

**Critical path: Phases 0-5 must complete in Week 1.** If they don't, the project is at risk. Phases 7 and 8 can run in parallel (dashboard and extension are independent). Phase 9 is a 2-hour task once Phase 5 works.
