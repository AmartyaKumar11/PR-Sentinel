"""Day-2 acceptance: webhook HMAC + GitHub client (mocked HTTP)."""

from __future__ import annotations

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

    body = launch_body("o/r", "fix it", "auto", "pr-sentinel/fix-1")
    assert "model" not in body
    assert body["source"]["repository"] == "https://github.com/o/r"
    assert body["target"]["branchName"] == "pr-sentinel/fix-1"
    assert body["target"]["autoCreatePr"] is True
    assert "[pr-sentinel]" in body["prompt"]["text"]
    named = launch_body("o/r", "fix it", None, None)
    assert "model" not in named
    picked = launch_body("o/r", "fix it", "gpt-4o-mini", None)
    assert picked["model"] == "gpt-4o-mini"
    assert model_label("auto") == "auto (Cursor picks)"
    assert public_status("FINISHED") == "completed"
    assert public_status("ERROR") == "failed"


def test_sentinel_pr_signals():
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

    monkeypatch.setattr(views.asyncio, "sleep", _sleep)
    monkeypatch.setattr(views.cursor, "get_run_status", _status)

    class Chan:
        def __init__(self):
            self.sent = []

        async def send(self, text):
            self.sent.append(text)

    chan = Chan()
    await views.monitor_agent("bc-test", chan, "task-1")
    assert calls["n"] == 3
    assert chan.sent == ["⚠️ Agent `bc-test` failed.\nboom"]


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
