"""Approve Fix must not post a deleted head ref to Cursor."""

import logging

import httpx
import pytest

from app.database import get_db
from app.discord.views import _approve_fix
from app.services.cursor_client import cursor
from app.services.github_client import GitHubClient
from app.services.task_manager import create_task, get_task

_REPO = "AmartyaKumar11/pr-sentinel-demo"
_REF = "chore/polyglot-baseline"
_PR_URL = f"https://github.com/{_REPO}/pull/70"


def _pr(*, state: str, merged: bool) -> dict:
    return {
        "title": "polyglot",
        "body": "",
        "head": {"sha": "abc", "ref": _REF},
        "state": state,
        "merged": merged,
        "html_url": _PR_URL,
        "draft": False,
        "node_id": "n",
        "mergeable": True,
    }


class _Follow:
    def __init__(self):
        self.sent = []

    async def send(self, content, ephemeral=False, **_kwargs):
        self.sent.append((content, ephemeral))


class _Interaction:
    def __init__(self):
        self.followup = _Follow()
        self.channel = None


def _response(status: int, url: str, json: dict | None = None, text: str = "") -> httpx.Response:
    kwargs = {"request": httpx.Request("GET", url)}
    if json is not None:
        return httpx.Response(status, json=json, **kwargs)
    return httpx.Response(status, text=text, **kwargs)


def _patch_github(monkeypatch, pr: dict, branch_status: int, hits: list):
    real = GitHubClient.__init__

    def init(self, *args, **kwargs):
        real(self, *args, **kwargs)

        async def get(url, **_kwargs):
            hits.append(str(url))
            if "/pulls/" in str(url):
                return _response(200, "https://api.github.com/pull", json=pr)
            if "/branches/" in str(url):
                return _response(branch_status, "https://api.github.com/branch", json={"message": "Branch not found"})
            return _response(404, "https://api.github.com/missing", json={"message": str(url)})

        self._client.get = get

    monkeypatch.setattr(GitHubClient, "__init__", init)


def _patch_cursor(monkeypatch, posts: list, status: int = 201, text: str = ""):
    async def post(_path, json=None, **_kwargs):
        posts.append(json)
        if status >= 400:
            return _response(status, "https://api.cursor.com/v0/agents", text=text)
        return _response(
            status,
            "https://api.cursor.com/v0/agents",
            json={"id": "bc_test", "status": "CREATING", "target": {"branchName": _REF}},
        )

    monkeypatch.setattr(cursor._client, "post", post)
    monkeypatch.setattr(cursor, "api_key", "test-key")


async def _pending(task_id: str) -> None:
    await create_task(
        await get_db(),
        task_id,
        _REPO,
        70,
        "abc",
        "MEDIUM",
        "dispatch",
        {"blast_radius": {}, "intent_alignment": {}},
        {"affected_files_priority": [], "suggested_fix_approach": "fix"},
        "review",
        "fix the client",
    )


@pytest.mark.asyncio
async def test_merged_pr_is_refused_without_a_cursor_post(monkeypatch):
    hits, posts = [], []
    _patch_github(monkeypatch, _pr(state="closed", merged=True), 200, hits)
    _patch_cursor(monkeypatch, posts)
    await _pending("launch-merged")
    interaction = _Interaction()

    await _approve_fix(interaction, "launch-merged")

    task = await get_task(await get_db(), "launch-merged")
    assert posts == []
    assert not any("/branches/" in url for url in hits)
    assert task["status"] == "pending"
    assert not task["cursor_agent_id"]
    content, ephemeral = interaction.followup.sent[-1]
    assert ephemeral is True
    assert "PR #70 is merged" in content
    assert content.startswith("⚠️ Can't launch agent —")


@pytest.mark.asyncio
async def test_deleted_branch_is_refused_without_a_cursor_post(monkeypatch):
    hits, posts = [], []
    _patch_github(monkeypatch, _pr(state="open", merged=False), 404, hits)
    _patch_cursor(monkeypatch, posts)
    await _pending("launch-deleted")
    interaction = _Interaction()

    await _approve_fix(interaction, "launch-deleted")

    task = await get_task(await get_db(), "launch-deleted")
    assert posts == []
    assert any("chore%2Fpolyglot-baseline" in url for url in hits)
    assert task["status"] == "pending"
    assert not task["cursor_agent_id"]
    content, ephemeral = interaction.followup.sent[-1]
    assert ephemeral is True
    assert "chore/polyglot-baseline" in content
    assert "does not exist" in content


@pytest.mark.asyncio
async def test_live_branch_posts_pr_url_without_source_ref(monkeypatch):
    hits, posts = [], []
    _patch_github(monkeypatch, _pr(state="open", merged=False), 200, hits)
    _patch_cursor(monkeypatch, posts)

    result = await cursor.launch_on_pull(_REPO, 70, "fix the client")

    assert result["agent_id"] == "bc_test"
    body = posts[0]
    assert body["source"] == {"prUrl": _PR_URL}
    assert "ref" not in body["source"]
    assert "repository" not in body["source"]
    assert body["target"]["autoBranch"] is False
    assert "branchName" not in body["target"]


@pytest.mark.asyncio
async def test_cursor_error_body_is_logged(monkeypatch, caplog):
    hits, posts = [], []
    _patch_github(monkeypatch, _pr(state="open", merged=False), 200, hits)
    _patch_cursor(monkeypatch, posts, status=400, text='{"error":"Branch \'chore/polyglot-baseline\' does not exist."}')

    with caplog.at_level(logging.ERROR, logger="app.services.cursor_client"):
        with pytest.raises(httpx.HTTPStatusError):
            await cursor.launch_on_pull(_REPO, 70, "fix the client")

    assert "does not exist" in caplog.text
    assert posts and posts[0]["source"]["prUrl"] == _PR_URL
