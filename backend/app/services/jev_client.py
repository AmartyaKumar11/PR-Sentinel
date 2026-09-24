"""Jev (TypeSafe AI) decision client — classification / confidence only."""

from __future__ import annotations

from app.config import settings


class JevClient:
    def __init__(self):
        from typesafe_sdk import AsyncTypeSafeClient

        self.client = AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key or None,
            model=settings.JEV_MODEL,
        )

    async def evaluate(self, state: dict, questions: dict) -> dict:
        """Single parallel pass — all questions answered at once."""
        response = await self.client.system_one(state=state, questions=questions)
        # SDK exposes answers; some versions also mirror as choices
        return getattr(response, "answers", None) or getattr(response, "choices", {})

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None)
        if close:
            await close()
