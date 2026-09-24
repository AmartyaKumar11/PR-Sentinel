"""Keep pytest off the network and off a file database.

Importing the app builds AgentOrchestrator, which used to open real
DeepSeek and Jev HTTP clients and never close them. On Linux that
hangs the interpreter after the tests have already passed.
"""

from __future__ import annotations

import asyncio
import functools
import os

os.environ["DATABASE_PATH"] = ":memory:"

import app.services.jev_client as jev_mod
import app.services.llm_client as llm_mod
import pytest


class _FakeLLM:
    async def chat(self, system: str, messages: list) -> str:
        return "mocked review"


class _FakeJev:
    async def evaluate(self, state: dict, questions: dict) -> dict:
        return {name: {"noul": 0.1, "answer": "no"} for name in questions}

    async def aclose(self) -> None:
        return None


llm_mod.LLMClient = _FakeLLM
jev_mod.JevClient = _FakeJev


@pytest.fixture(autouse=True)
def _dont_run_agent(monkeypatch):
    import app.routes.webhook as webhook

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(webhook._agent, "run", _noop)


@pytest.fixture(autouse=True)
async def _close_db():
    yield
    from app.database import close_db

    await close_db()


def pytest_collection_modifyitems(items):
    for item in items:
        fn = getattr(item, "obj", None)
        if fn is None or not asyncio.iscoroutinefunction(fn):
            continue

        @functools.wraps(fn)
        async def _wrapped(*args, _fn=fn, **kwargs):
            return await asyncio.wait_for(_fn(*args, **kwargs), timeout=5)

        item.obj = _wrapped
