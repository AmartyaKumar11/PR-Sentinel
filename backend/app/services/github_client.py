"""GitHub REST API wrapper (BUILD-SEQUENCE Phase 1.1)."""

from __future__ import annotations

import base64
import re

import httpx

from app.config import settings


class GitHubClient:
    def __init__(self, token: str | None = None, owner: str | None = None, repo: str | None = None):
        self.owner = owner
        self.repo = repo
        self.token = token if token is not None else settings.GITHUB_TOKEN
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # gho_ = gh auth oauth; ghp_/github_pat_ = classic/fine-grained PATs
        if (
            self.token
            and self.token.startswith(("ghp_", "github_pat_", "gho_"))
            and "YOUR_" not in self.token
        ):
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=30.0,
        )

    def set_repo(self, full_name: str) -> None:
        self.owner, self.repo = full_name.split("/", 1)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        r = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        r.raise_for_status()
        text = r.text
        if len(text) <= 12000:
            return text
        return text[:6000] + "\n... truncated ...\n" + text[-6000:]

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        r = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")
        r.raise_for_status()
        return [
            {
                "filename": f.get("filename", ""),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "patch": f.get("patch") or "",
            }
            for f in r.json()
        ]

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> dict:
        r = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        r.raise_for_status()
        pr = r.json()
        return {
            "title": pr.get("title", ""),
            "body": pr.get("body") or "",
            "head_sha": (pr.get("head") or {}).get("sha", ""),
            "branch": (pr.get("head") or {}).get("ref", ""),
            "state": pr.get("state", ""),
        }

    async def get_linked_issue(self, owner: str, repo: str, pr_number: int) -> dict | None:
        info = await self.get_pr_info(owner, repo, pr_number)
        issue_num = _find_issue_number(info["body"], info["branch"])
        if issue_num is None:
            return None
        ir = await self._client.get(f"/repos/{owner}/{repo}/issues/{issue_num}")
        if ir.status_code == 404:
            return None
        ir.raise_for_status()
        issue = ir.json()
        return {
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": (issue.get("body") or "")[:2000],
            "labels": [lb.get("name") for lb in issue.get("labels", [])],
            "state": issue.get("state"),
        }

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        r = await self._client.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) <= 200:
            return content
        # Keep a middle window around line 100
        start = max(0, 90)
        end = min(len(lines), 110)
        return "\n".join(lines[start:end])

    async def get_file_tree(
        self, owner: str, repo: str, ref: str, path_filter: str = ""
    ) -> list[str]:
        r = await self._client.get(
            f"/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        r.raise_for_status()
        out = []
        for item in r.json().get("tree", []):
            if item.get("type") != "blob":
                continue
            p = item["path"]
            if path_filter and not p.startswith(path_filter):
                continue
            if not p.endswith(".py"):
                continue
            out.append(p)
        return out

    async def post_comment(self, owner: str, repo: str, pr_number: int, body: str) -> dict:
        r = await self._client.post(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        r.raise_for_status()
        data = r.json()
        return {"id": data.get("id"), "html_url": data.get("html_url")}

    async def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        r = await self._client.post(f"/repos/{owner}/{repo}/issues", json=payload)
        r.raise_for_status()
        data = r.json()
        return {"number": data.get("number"), "id": data.get("id"), "html_url": data.get("html_url")}

    # ── aliases used by earlier tools/tests ──
    async def fetch_pr_diff(self, pr_number: int, max_chars: int = 12000) -> str:
        text = await self.get_pr_diff(self.owner, self.repo, pr_number)
        return text[:max_chars]

    async def fetch_linked_issue(self, pr_number: int) -> dict | None:
        return await self.get_linked_issue(self.owner, self.repo, pr_number)

    async def fetch_file_content(self, path: str, ref: str) -> str:
        return await self.get_file_content(self.owner, self.repo, path, ref)

    async def list_files(self, ref: str, path_filter: str = "") -> list[str]:
        return await self.get_file_tree(self.owner, self.repo, ref, path_filter)

    async def post_pr_review(self, pr_number: int, review_body: str) -> dict:
        result = await self.post_comment(self.owner, self.repo, pr_number, review_body)
        return {"comment_id": result.get("id"), "url": result.get("html_url")}


def _find_issue_number(body: str, branch: str) -> int | None:
    m = re.search(r"#(\d+)", body or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(?:feature|fix|issue)[/-](\d+)", branch or "")
    if m:
        return int(m.group(1))
    return None
