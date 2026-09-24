"""
End-to-end agent test. Run manually:
    python test_agent_e2e.py <owner> <repo> <pr_number>

Example:
    python test_agent_e2e.py AmartyaKumar11 pr-sentinel-demo 1
"""

from __future__ import annotations

import asyncio
import sys
import uuid


async def main():
    if len(sys.argv) < 4:
        print("Usage: python test_agent_e2e.py <owner> <repo> <pr_number>")
        sys.exit(1)

    owner, repo, pr_number = sys.argv[1], sys.argv[2], int(sys.argv[3])

    from app.agent.orchestrator import AgentOrchestrator
    from app.database import get_db
    from app.services.github_client import GitHubClient
    from app.services.sse_manager import sse_manager

    agent = AgentOrchestrator()
    task_id = str(uuid.uuid4())
    print(f"Task ID: {task_id}")
    print(f"Analyzing PR #{pr_number} on {owner}/{repo}...")
    print("=" * 60)

    events = []

    async def collector():
        async for event in sse_manager.subscribe(task_id):
            events.append(event)
            phase = event.get("phase", "?")
            etype = event.get("type", "?")
            content = str(event.get("content", ""))[:100]
            tool = event.get("tool", "")
            print(f"  [{phase}] {etype}: {tool + ' ' if tool else ''}{content}")

    collector_task = asyncio.create_task(collector())
    await asyncio.sleep(0.05)

    gh = GitHubClient(owner=owner, repo=repo)
    pr_info = await gh.get_pr_info(owner, repo, pr_number)
    head_sha = pr_info["head_sha"]
    print(f"Head SHA: {head_sha[:12]}...")

    await agent.run(task_id, owner, repo, pr_number, head_sha, mode="full")
    await asyncio.sleep(0.5)
    collector_task.cancel()
    try:
        await collector_task
    except asyncio.CancelledError:
        pass

    print("=" * 60)
    print(f"Total SSE events: {len(events)}")

    db = await get_db()
    cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = await cursor.fetchone()
    if row:
        task = dict(row)
        print(f"\nSeverity: {task['severity']}")
        print(f"Action: {task['action']}")
        print(f"Status: {task['status']}")
        if task.get("jev_confidences"):
            print(f"Jev confidences: {task['jev_confidences']}")
        if task.get("requirement_scores"):
            print(f"Requirement scores: {task['requirement_scores']}")
        print(f"\nComposer prompt:\n{(task.get('composer_prompt') or 'N/A')[:500]}")
    else:
        print("ERROR: No task found in DB!")
        sys.exit(1)

    # ponytail: httpx/OpenAI leave open sockets; hard-exit after asserts
    import os

    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
