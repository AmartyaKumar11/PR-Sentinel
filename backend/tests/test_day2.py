"""Day-2 acceptance: webhook HMAC + GitHub client (mocked HTTP)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Response

# Ensure app imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.main import app
from app.services.github_client import GitHubClient, _find_issue_number
from app.agent.tools.github_tools import register_github_tools
from app.agent.tools.registry import ToolRegistry


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()


def _pr_payload(action: str = "opened", pr: int = 1, sha: str = "abc123") -> bytes:
    return json.dumps(
        {
            "action": action,
            "pull_request": {
                "number": pr,
                "body": "Fixes #1",
                "head": {"sha": sha, "ref": "feature/1-password-reset"},
            },
            "repository": {"full_name": "AmartyaKumar11/PR-Sentinel"},
        }
    ).encode()


@pytest.mark.asyncio
async def test_webhook_valid_returns_202():
    body = _pr_payload()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body),
            },
        )
    assert r.status_code == 202
    assert "pending_id" in r.json()


@pytest.mark.asyncio
async def test_webhook_invalid_sig_401():
    body = _pr_payload()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=deadbeef",
            },
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_non_pr_skipped():
    body = _pr_payload()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign(body),
            },
        )
    assert r.status_code == 200
    assert r.json()["skipped"] is True


@pytest.mark.asyncio
async def test_webhook_synchronize_reuses_task_id():
    """opened then synchronize on same PR must reuse task_id (Bug 4)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed a live task via opened
        body1 = _pr_payload(action="opened", pr=99, sha="aaa")
        r1 = await client.post(
            "/api/webhook/github",
            content=body1,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body1),
            },
        )
        assert r1.status_code == 202
        assert "pending_id" in r1.json()
        from app.database import get_db
        from app.services.task_manager import create_task

        task_id = "sync-reuse-task"
        db = await get_db()
        cur = await db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,))
        if not await cur.fetchone():
            await create_task(
                db,
                task_id,
                "AmartyaKumar11/PR-Sentinel",
                99,
                "aaa",
                "MEDIUM",
                "dispatch",
                {"blast_radius": {}, "intent_alignment": {"missing": [], "scope_creep": [], "addressed": []}},
                {"affected_files_priority": [], "suggested_fix_approach": "n/a"},
                "review",
                "prompt",
            )

        body2 = _pr_payload(action="synchronize", pr=99, sha="bbb")
        r2 = await client.post(
            "/api/webhook/github",
            content=body2,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body2),
            },
        )
    assert r2.status_code == 202
    assert r2.json()["task_id"] == task_id


def test_launch_body_omits_auto_and_maps_status():
    from app.services.cursor_client import launch_body, model_label, public_status

    body = launch_body("o/r", "fix it", "auto", "feature/password-reset")
    assert "model" not in body
    assert body["source"]["repository"] == "https://github.com/o/r"
    assert body["source"]["ref"] == "feature/password-reset"
    assert body["target"]["branchName"] == "feature/password-reset"
    assert "autoCreatePr" not in body["target"]
    assert "Do NOT open a new pull request" in body["prompt"]["text"]
    assert "[pr-sentinel-fix]" in body["prompt"]["text"]
    named = launch_body("o/r", "fix it", None, None)
    assert "model" not in named
    picked = launch_body("o/r", "fix it", "gpt-4o-mini", None)
    assert picked["model"] == "gpt-4o-mini"
    assert model_label("auto") == "auto (Cursor picks)"
    assert public_status("FINISHED") == "completed"
    assert public_status("ERROR") == "failed"


def test_agent_fix_marker_is_not_a_human_commit():
    from app.routes.webhook import is_agent_fix

    assert is_agent_fix("[pr-sentinel-fix] validate the token")
    assert is_agent_fix("", login="cursoragent")
    assert is_agent_fix("", email="cursoragent@cursor.com")
    assert not is_agent_fix("Add password reset endpoint", login="AmartyaKumar11")
    from app.routes.webhook import sentinel_pr

    assert sentinel_pr({"head": {"ref": "pr-sentinel/fix-12"}, "body": "", "user": {"login": "me"}})
    assert sentinel_pr({"head": {"ref": "feat"}, "body": "See [pr-sentinel]", "user": {}})
    assert sentinel_pr({"head": {"ref": "feat"}, "body": "Opened by PR Sentinel", "user": {}})
    assert sentinel_pr({"head": {"ref": "feat"}, "body": "", "user": {"login": "cursoragent"}})
    assert not sentinel_pr(
        {"head": {"ref": "feature/1-password-reset"}, "body": "Fixes #1", "user": {"login": "AmartyaKumar11"}}
    )


@pytest.mark.asyncio
async def test_webhook_skips_sentinel_branch():
    body = json.dumps(
        {
            "action": "opened",
            "pull_request": {
                "number": 12,
                "body": "Closes #1",
                "head": {"sha": "abc", "ref": "pr-sentinel/fix-11"},
                "user": {"login": "AmartyaKumar11"},
            },
            "repository": {"full_name": "AmartyaKumar11/pr-sentinel-demo"},
        }
    ).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body),
            },
        )
    assert response.status_code == 200
    assert response.json()["reason"] == "PR created by PR Sentinel"


@pytest.mark.asyncio
async def test_webhook_skips_rediagnosis_for_agent_commit(monkeypatch):
    async def _agent(*_args, **_kwargs):
        return True

    monkeypatch.setattr("app.routes.webhook._head_is_agent_fix", _agent)
    body = _pr_payload(action="synchronize", pr=88001, sha="agentsha")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _sign(body),
            },
        )
    assert response.status_code == 200
    assert response.json()["reason"] == "agent fix commit"


def test_run_status_keeps_activity():
    from app.services.cursor_client import format_progress, format_status_reply, run_status_from

    status = run_status_from(
        {
            "status": "RUNNING",
            "name": "fix orders",
            "filesChanged": 2,
            "target": {"branchName": "pr-sentinel/fix-25"},
        },
        [
            {"type": "user_message", "text": "do the thing"},
            {"type": "assistant_message", "text": "Updating src/orders.py\nadding the check"},
        ],
    )
    assert status["status"] == "running"
    assert status["branch"] == "pr-sentinel/fix-25"
    assert status["files_changed_count"] == 2
    assert status["current_step"] == "Updating src/orders.py adding the check"
    opening = run_status_from(
        {"status": "RUNNING", "createdAt": "2026-09-24T16:04:13Z", "target": {}},
        [
            {"type": "assistant_message", "text": "I'll start by reading the file"},
            {"type": "assistant_message", "text": "Removing create_invoice from billing.py"},
        ],
    )
    assert opening["current_step"] == "Removing create_invoice from billing.py"
    assert "I'll start" not in opening["current_step"]
    reply = format_status_reply("bc-test", status)
    assert "**Files:** 2" in reply
    assert "Last update:" in reply
    listed = run_status_from(
        {"status": "RUNNING", "filesChanged": ["src/orders.py", "tests/test_orders.py"], "target": {}},
        [],
    )
    from datetime import datetime, timezone

    from app.services.cursor_client import elapsed_since

    assert elapsed_since(
        "2026-09-24T16:04:13+00:00",
        datetime(2026, 9, 24, 16, 6, 28, tzinfo=timezone.utc),
    ) == "2m 15s"
    progress = format_progress(25, listed)
    assert "src/orders.py" in progress
    assert "PR #25" in progress
    assert "PR: not yet" in progress


@pytest.mark.asyncio
async def test_monitor_retries_then_reports_failure(monkeypatch):
    from app.discord import views

    async def _sleep(_seconds):
        return None

    calls = {"n": 0}

    async def _status(_agent_id):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("404")
        return {"status": "failed", "result_text": "boom"}

    async def _no_task(_db, _task_id):
        return None

    monkeypatch.setattr(views.asyncio, "sleep", _sleep)
    monkeypatch.setattr(views.cursor, "get_run_status", _status)
    monkeypatch.setattr(views, "get_task", _no_task)

    class Msg:
        def __init__(self, text):
            self.content = text

        async def edit(self, content=None, **_kwargs):
            if content is not None:
                self.content = content

    class Chan:
        def __init__(self):
            self.sent = []

        async def send(self, text):
            msg = Msg(text)
            self.sent.append(msg)
            return msg

    chan = Chan()
    await views.monitor_agent("bc-test", chan, "task-1")
    assert calls["n"] == 3
    assert len(chan.sent) == 1
    assert chan.sent[0].content == "⚠️ Agent `bc-test` failed.\nboom"


def test_find_issue_from_body_and_branch():
    assert _find_issue_number("Closes #12", "main") == 12
    assert _find_issue_number("", "feature/1-password-reset") == 1
    assert _find_issue_number("", "fix/2-order-validation") == 2
    assert _find_issue_number("", "feature/no-issue-billing") is None


@pytest.mark.asyncio
async def test_github_client_four_methods_mocked():
    def handler(request: httpx.Request) -> Response:
        path = request.url.path
        accept = request.headers.get("accept", "")
        if path.endswith("/pulls/7") and "diff" in accept:
            return Response(
                200,
                text="diff --git a/src/auth.py b/src/auth.py\n+def reset_password(email):\n+    pass\n",
            )
        if path.endswith("/pulls/7/files"):
            return Response(
                200,
                json=[
                    {
                        "filename": "src/auth.py",
                        "patch": "@@\n+def reset_password(email):\n+    pass\n",
                    }
                ],
            )
        if path.endswith("/pulls/7"):
            return Response(
                200,
                json={
                    "number": 7,
                    "body": "Implements #3",
                    "head": {"ref": "feature/3-x", "sha": "fff"},
                },
            )
        if path.endswith("/issues/3"):
            return Response(
                200,
                json={
                    "number": 3,
                    "title": "Do the thing",
                    "body": "- validate\n- expire",
                    "labels": [{"name": "bug"}],
                    "state": "open",
                },
            )
        if "/contents/" in path:
            import base64

            raw = b"def hello():\n    return 1\n"
            return Response(
                200,
                json={"content": base64.b64encode(raw).decode(), "encoding": "base64"},
            )
        if path.endswith("/issues/7/comments") and request.method == "POST":
            return Response(201, json={"id": 555, "html_url": "https://github.com/x/y/issues/7#issuecomment-555"})
        return Response(404, json={"message": "not found", "path": path})

    transport = MockTransport(handler)
    gh = GitHubClient(token="fake", owner="o", repo="r")
    await gh._client.aclose()
    gh._client = httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=transport,
        headers={"Authorization": "Bearer fake", "Accept": "application/vnd.github+json"},
    )

    diff = await gh.fetch_pr_diff(7)
    assert "reset_password" in diff
    assert "src/auth.py" in diff

    issue = await gh.fetch_linked_issue(7)
    assert issue is not None
    assert issue["number"] == 3
    assert issue["title"] == "Do the thing"

    content = await gh.fetch_file_content("src/auth.py", "main")
    assert "def hello" in content

    posted = await gh.post_pr_review(7, "## review")
    assert posted["comment_id"] == 555
    assert "issuecomment" in posted["url"]

    registry = ToolRegistry()
    register_github_tools(registry, gh)
    assert set(registry.names()) == {
        "fetch_pr_diff",
        "fetch_linked_issue",
        "fetch_file_content",
        "post_pr_review",
    }
    tool_diff = await registry.execute("fetch_pr_diff", pr_number=7)
    assert "reset_password" in tool_diff

    await gh.close()


async def test_merge_pr_safe_marks_draft_ready(monkeypatch):
    seen = []

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.github_client.asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> Response:
        seen.append(request.method + " " + request.url.path)
        if request.url.path.endswith("/pulls/24") and request.method == "GET":
            return Response(200, json={"draft": True, "node_id": "PR_kwDO", "title": "t", "body": "", "head": {}, "state": "open"})
        if request.url.path == "/graphql":
            return Response(200, json={"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}})
        if request.method == "PUT":
            return Response(200, json={"merged": True, "message": "merged"})
        return Response(404)

    gh = GitHubClient(token="fake", owner="o", repo="r")
    await gh._client.aclose()
    gh._client = httpx.AsyncClient(base_url="https://api.github.com", transport=MockTransport(handler))
    result = await gh.merge_pr_safe("o", "r", 24)
    await gh.close()
    assert result["merged"] is True
    assert "POST /graphql" in seen


async def test_merge_conflict_stays_a_sentence():
    def handler(request: httpx.Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"draft": False, "mergeable": False, "title": "t", "body": "", "head": {}, "state": "open"})
        return Response(
            405,
            json={"message": "Pull Request has merge conflicts", "documentation_url": "https://docs.github.com/rest/pulls"},
        )

    gh = GitHubClient(token="fake", owner="o", repo="r")
    await gh._client.aclose()
    gh._client = httpx.AsyncClient(base_url="https://api.github.com", transport=MockTransport(handler))
    result = await gh.merge_pr_safe("o", "r", 53)
    await gh.close()
    assert result["merged"] is False
    assert "merge conflicts" in result["message"]
    assert "documentation_url" not in result["message"]


async def test_update_branch_sends_head_sha():
    seen = {}

    def handler(request: httpx.Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"head": {"sha": "abc123", "ref": "feature/x"}, "draft": False})
        seen["body"] = json.loads(request.content.decode())
        seen["path"] = request.url.path
        return Response(202, json={"message": "Updating pull request branch.", "url": "https://api.github.com"})

    gh = GitHubClient(token="fake", owner="o", repo="r")
    await gh._client.aclose()
    gh._client = httpx.AsyncClient(base_url="https://api.github.com", transport=MockTransport(handler))
    result = await gh.update_branch("o", "r", 60)
    await gh.close()
    assert result["ok"] is True
    assert seen["path"].endswith("/pulls/60/update-branch")
    assert seen["body"] == {"expected_head_sha": "abc123"}


async def test_stale_base_rebases_then_merges(monkeypatch):
    sent = []

    class Channel:
        async def send(self, text):
            sent.append(text)

    class FakeGH:
        async def merge_pr_safe(self, owner, repo, number, method="squash", ignore_mergeable=False):
            if ignore_mergeable:
                return {"merged": True, "message": "merged"}
            return {
                "merged": False,
                "message": "This pull request has merge conflicts with the base branch.",
            }

        async def update_branch(self, owner, repo, number):
            return {"ok": True, "head_sha": "old", "message": ""}

        async def get_pr_info(self, owner, repo, number):
            return {"head_sha": "new", "mergeable": True, "branch": "feature/1-password-reset"}

        async def close(self):
            return None

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.discord.views.GitHubClient", lambda *a, **k: FakeGH())
    monkeypatch.setattr("app.discord.views.asyncio.sleep", _no_sleep)
    from app.discord.views import merge_with_conflict_resolution

    result = await merge_with_conflict_resolution("o", "r", 60, Channel())
    assert result["merged"] is True
    assert result["announced"] is True
    assert any("Resolving conflicts" in line for line in sent)
    assert any("Rebased and merged" in line for line in sent)
    assert not any("launching agent" in line for line in sent)


async def test_real_conflict_launches_agent_on_same_branch(monkeypatch):
    sent = []
    launched = {}

    class Channel:
        async def send(self, text):
            sent.append(text)

    class FakeGH:
        async def merge_pr_safe(self, owner, repo, number, method="squash", ignore_mergeable=False):
            return {
                "merged": False,
                "message": "This pull request has merge conflicts with the base branch.",
            }

        async def update_branch(self, owner, repo, number):
            return {"ok": False, "head_sha": "old", "message": "merge conflict"}

        async def get_pr_info(self, owner, repo, number):
            return {"head_sha": "old", "mergeable": False, "branch": "feature/1-password-reset"}

        async def close(self):
            return None

    async def fake_launch(repo, prompt, model=None, branch=None):
        launched["repo"] = repo
        launched["branch"] = branch
        launched["prompt"] = prompt
        return {"agent_id": "bc-conflict"}

    async def fake_monitor(*args, **kwargs):
        launched["monitored"] = args[0] if args else kwargs.get("agent_id")

    async def fake_remember(*args, **kwargs):
        return None

    monkeypatch.setattr("app.discord.views.GitHubClient", lambda *a, **k: FakeGH())
    monkeypatch.setattr("app.discord.views.cursor.launch_agent", fake_launch)
    monkeypatch.setattr("app.discord.views.monitor_conflict_resolution", fake_monitor)
    monkeypatch.setattr("app.discord.views._remember_agent", fake_remember)
    from app.discord.views import merge_with_conflict_resolution

    result = await merge_with_conflict_resolution("o", "r", 60, Channel())
    await asyncio.sleep(0)
    assert result["announced"] is True
    assert result["agent_id"] == "bc-conflict"
    assert launched["branch"] == "feature/1-password-reset"
    assert "Do NOT open a new PR" in launched["prompt"]
    assert "git push --force origin feature/1-password-reset" in launched["prompt"]
    assert any("launching agent" in line for line in sent)
    assert launched["monitored"] == "bc-conflict"


if __name__ == "__main__":
    import asyncio

    async def _main():
        await test_webhook_valid_returns_202()
        await test_webhook_invalid_sig_401()
        await test_webhook_non_pr_skipped()
        await test_webhook_synchronize_reuses_task_id()
        test_find_issue_from_body_and_branch()
        await test_github_client_four_methods_mocked()
        print("day2_ok")
        import os
        os._exit(0)

    asyncio.run(_main())
