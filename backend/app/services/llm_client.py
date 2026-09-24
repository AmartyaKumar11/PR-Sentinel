"""DeepSeek chat client (non-reasoning)."""

from openai import AsyncOpenAI

from app.config import settings


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

    async def chat(self, system: str, messages: list[dict]) -> str:
        if not settings.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}] + messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            LLMClient.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        return response.choices[0].message.content or ""
