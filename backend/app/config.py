from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GITHUB_WEBHOOK_SECRET: str = "dev_secret_change_in_prod"
    GITHUB_TOKEN: str = ""

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-v4-flash"
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.1

    # Jev (TypeSafe AI) — TYPESAFE_API_KEY preferred; JEV_API_KEY accepted as alias
    TYPESAFE_API_KEY: str = ""
    JEV_API_KEY: str = ""
    JEV_MODEL: str = "jev-1.13.0"

    APP_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:5173"
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    DATABASE_PATH: str = "./sentinel.db"
    LOG_LEVEL: str = "debug"

    DISCORD_BOT_TOKEN: str = ""
    DISCORD_CHANNEL_ID: str = ""
    DISCORD_GUILD_ID: str = ""

    CURSOR_API_KEY: str = ""
    CURSOR_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def typesafe_api_key(self) -> str:
        return self.TYPESAFE_API_KEY or self.JEV_API_KEY


settings = Settings()
