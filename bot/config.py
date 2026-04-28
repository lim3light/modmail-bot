from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Discord ────────────────────────────────────────────────────────────────
    discord_token: str
    discord_guild_id: int

    role_approved: int
    role_visitor: int
    role_unverified: int

    modmail_category_id: int
    mod_log_channel_id: int

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── AI ─────────────────────────────────────────────────────────────────────
    llm_provider: str = "mock"          # anthropic | openai | mock
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "claude-sonnet-4-20250514"

    confidence_approve: float = Field(default=0.80, ge=0.0, le=1.0)
    confidence_visitor: float = Field(default=0.50, ge=0.0, le=1.0)
    confidence_reject: float = Field(default=0.75, ge=0.0, le=1.0)

    llm_max_retries: int = 3
    llm_timeout_seconds: int = 30
    max_question_rounds: int = 3

    server_context: str = "A community Discord server."

    # ── App ────────────────────────────────────────────────────────────────────
    env: str = "development"
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def confidence_thresholds(self) -> dict[str, float]:
        return {
            "APPROVE": self.confidence_approve,
            "VISITOR": self.confidence_visitor,
            "REJECT": self.confidence_reject,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
