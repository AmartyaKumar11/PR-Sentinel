"""GitHub API client for PR Sentinel tools."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from app.config import settings


class GitHubClient:
    def __init__(self, token: str | None = None, owner: str | None = None, repo: str | None = None):
        self.token = token or settings.GITHUB_TOKEN
        self.owner = owner
        self.repo = repo
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def set_repo(self, full_name: str) -> None:
        parts = full_name.split("/", 1)
        self.owner, self.repo = parts[0], parts[1]

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_pr_diff(self, pr_number: int, max_chars: int = 12000) -> str:
        """Unified-ish diff from PR files, smart-truncated to ~3000 tokens."""
        r = await self._client.get(f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/files")
        r.raise_for_status()
        files = r.json()
        chunks: list[str] = []
        for f in files:
            path = f.get("filename", "")
            patch = f.get("patch") or ""
            chunks.append(f"--- {path}\n{patch}")
        text = "\n\n".join(chunks)
        if len(text) <= max_chars:
            return text
        # Truncate to signatures + short context
        return _smart_truncate(text, max_chars)

    async def fetch_linked_issue(self, pr_number: int) -> dict | None:
        r = await self._client.get(f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}")
        r.raise_for_status()
        pr = r.json()
        body = pr.get("body") or ""
        branch = (pr.get("head") or {}).get("ref") or ""

        issue_num = _find_issue_number(body, branch)
        if issue_num is None:
            return None

        ir = await self._client.get(f"/repos/{self.owner}/{self.repo}/issues/{issue_num}")
        if ir.status_code == 404:
            return None
        ir.raise_for_status()
        issue = ir.json()
        issue_body = (issue.get("body") or "")[:2000]
        return {
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": issue_body,
            "labels": [lb.get("name") for lb in issue.get("labels", [])],
            "state": issue.get("state"),
        }

    async def fetch_file_content(self, path: str, ref: str) -> str:
        r = await self._client.get(
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            params={"ref": ref},
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) > 200:
            # Return middle-ish slice — caller usually wants changed fn context
            return "\n".join(lines[:200])
        return content

    async def list_files(self, ref: str, path_filter: str = "") -> list[str]:
        """Recursive tree listing, filtered by path prefix."""
        r = await self._client.get(
            f"/repos/{self.owner}/{self.repo}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        r.raise_for_status()
        tree = r.json().get("tree", [])
        out = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            p = item["path"]
            if path_filter and not p.startswith(path_filter):
                continue
            out.append(p)
        return out

    async def post_pr_review(self, pr_number: int, review_body: str) -> dict:
        r = await self._client.post(
            f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            json={"body": review_body},
        )
        r.raise_for_status()
        data = r.json()
        return {"comment_id": data.get("id"), "url": data.get("html_url")}


def _find_issue_number(body: str, branch: str) -> int | None:
    m = re.search(r"#(\d+)", body)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:feature|fix)/(\d+)-", branch)
    if m:
        return int(m.group(1))
    return None


def _smart_truncate(text: str, max_chars: int) -> str:
    """Keep function/class signatures and ±5 context lines when truncating."""
    lines = text.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("def ") or line.startswith("class ") or line.startswith("async def ") or line.startswith("---"):
            start = max(0, i - 5)
            end = min(len(lines), i + 6)
            keep.extend(lines[start:end])
            keep.append("...")
    out = "\n".join(keep)
    return out[:max_chars] if out else text[:max_chars]
