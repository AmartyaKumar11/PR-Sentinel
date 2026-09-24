"""Register all 6 agent tools for a repo context."""

from __future__ import annotations

import json

from app.agent.tools.registry import ToolDef, ToolRegistry
from app.services import cache as cache_svc
from app.services.ast_parser import build_graph
from app.services.blast_radius import trace
from app.services.diff_parser import parse_diff
from app.services.github_client import GitHubClient


def build_tool_registry(owner: str, repo: str, gh: GitHubClient | None = None) -> ToolRegistry:
    gh = gh or GitHubClient(owner=owner, repo=repo)
    gh.owner, gh.repo = owner, repo
    reg = ToolRegistry()

    async def fetch_pr_diff(pr_number: int) -> dict:
        try:
            raw = await gh.get_pr_diff(owner, repo, pr_number)
            parsed = parse_diff(raw)
            return {"diff": raw, "parsed": parsed, "files": [f["path"] for f in parsed["files"]]}
        except Exception as e:
            return {"error": str(e)}

    async def fetch_linked_issue(pr_number: int) -> dict | None:
        try:
            return await gh.get_linked_issue(owner, repo, pr_number)
        except Exception as e:
            return {"error": str(e)}

    async def build_dependency_graph(ref: str, path_filter: str = "src/") -> dict:
        try:
            key = f"depgraph:{owner}/{repo}:{ref}:{path_filter}"
            cached = await cache_svc.cache_get(key)
            # An empty graph is a failed parse (UTF-8 BOM), not a valid SHA snapshot.
            if cached and cached.get("nodes"):
                return cached
            paths = await gh.get_file_tree(owner, repo, ref, path_filter)
            files: dict[str, str] = {}
            for p in paths:
                try:
                    files[p] = await gh.get_file_content(owner, repo, p, ref)
                except Exception:
                    continue
            graph = build_graph(files)
            if graph.get("nodes"):
                await cache_svc.cache_set(key, graph)
            return graph
        except Exception as e:
            return {"error": str(e), "nodes": [], "edges": []}

    async def trace_blast_radius(changed_identifiers: list, dep_graph: dict | None = None) -> dict:
        try:
            graph = dep_graph or {"nodes": [], "edges": []}
            if isinstance(changed_identifiers, str):
                changed_identifiers = json.loads(changed_identifiers)
            if isinstance(graph, str):
                graph = json.loads(graph)
            return trace(changed_identifiers, graph)
        except Exception as e:
            return {"error": str(e), "risk_score": 0, "depth_1_impacted": [], "depth_2_impacted": []}

    async def fetch_file_content(path: str, ref: str) -> str:
        try:
            return await gh.get_file_content(owner, repo, path, ref)
        except Exception as e:
            return f"Error: {e}"

    async def post_pr_review(pr_number: int, review_body: str) -> dict:
        try:
            return await gh.post_comment(owner, repo, pr_number, review_body)
        except Exception as e:
            return {"error": str(e)}

    reg.register(
        ToolDef(
            "fetch_pr_diff",
            "Fetch unified diff for a PR (truncated).",
            {"pr_number": "int"},
            fetch_pr_diff,
        )
    )
    reg.register(
        ToolDef(
            "fetch_linked_issue",
            "Find and fetch the linked GitHub issue.",
            {"pr_number": "int"},
            fetch_linked_issue,
        )
    )
    reg.register(
        ToolDef(
            "build_dependency_graph",
            "Build Python AST dependency graph at a ref.",
            {"ref": "string", "path_filter": "string"},
            build_dependency_graph,
        )
    )
    reg.register(
        ToolDef(
            "trace_blast_radius",
            "BFS blast radius from changed identifiers.",
            {"changed_identifiers": "list[string]", "dep_graph": "object"},
            trace_blast_radius,
        )
    )
    reg.register(
        ToolDef(
            "fetch_file_content",
            "Fetch raw file content at a git ref.",
            {"path": "string", "ref": "string"},
            fetch_file_content,
        )
    )
    reg.register(
        ToolDef(
            "post_pr_review",
            "Post a markdown comment on the PR.",
            {"pr_number": "int", "review_body": "string"},
            post_pr_review,
        )
    )
    return reg


# back-compat
def register_github_tools(registry: ToolRegistry, gh: GitHubClient) -> None:
    owner, repo = gh.owner or "o", gh.repo or "r"
    built = build_tool_registry(owner, repo, gh)
    for name in built.names():
        registry.register(built._tools[name])
