"""DeepSeek chat client (non-reasoning)."""

import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    total_tokens = 0

    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY or "unused",
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        self.model = settings.LLM_MODEL
        self.max_tokens = settings.LLM_MAX_TOKENS
        self.temperature = settings.LLM_TEMPERATURE

    async def chat(self, system: str, messages: list[dict], *, thinking: bool = True) -> str:
        if not settings.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        kwargs = {}
        # Thinking mode can spend the whole token budget and leave content empty.
        if not thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}] + messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            **kwargs,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            LLMClient.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        message = response.choices[0].message
        content = _message_text(message)
        if not content:
            finish = response.choices[0].finish_reason
            reasoning = getattr(message, "reasoning_content", None) or ""
            logger.warning(
                "deepseek empty content finish=%s reasoning_chars=%s",
                finish,
                len(reasoning),
            )
        return content


def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(getattr(part, "text", "") or ""))
        content = "".join(parts)
    return content or ""
