"""GitHub-backed agent tools (M-03)."""

from __future__ import annotations

from app.agent.tools.registry import ToolDef, ToolRegistry
from app.services.github_client import GitHubClient


def register_github_tools(registry: ToolRegistry, gh: GitHubClient) -> None:
    registry.register(
        ToolDef(
            name="fetch_pr_diff",
            description="Fetch unified diff for a PR, smart-truncated to ~3000 tokens.",
            parameters={"pr_number": "int — the pull request number"},
            fn=gh.fetch_pr_diff,
        )
    )
    registry.register(
        ToolDef(
            name="fetch_linked_issue",
            description="Find and fetch the linked GitHub issue for a PR.",
            parameters={"pr_number": "int — the pull request number"},
            fn=gh.fetch_linked_issue,
        )
    )
    registry.register(
        ToolDef(
            name="fetch_file_content",
            description="Fetch raw file content at a git ref.",
            parameters={
                "path": "string — file path in repo",
                "ref": "string — commit SHA or branch",
            },
            fn=gh.fetch_file_content,
        )
    )
    registry.register(
        ToolDef(
            name="post_pr_review",
            description="Post a comment on the PR.",
            parameters={
                "pr_number": "int",
                "review_body": "string — markdown formatted review",
            },
            fn=gh.post_pr_review,
        )
    )
