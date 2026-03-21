from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PERSONAL_AGENT_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "personal-agent"
    environment: str = "development"
    log_level: str = "INFO"

    api_host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("PERSONAL_AGENT_API_HOST", "HOST"))
    api_port: int = Field(default=8000, validation_alias=AliasChoices("PERSONAL_AGENT_API_PORT", "PORT"))

    discord_bot_token: SecretStr | None = None
    discord_command_prefix: str = "!"
    discord_command_channel_id: int | None = None
    discord_summary_channel_id: int | None = None
    discord_interesting_channel_id: int | None = None
    discord_opportunities_channel_id: int | None = None
    discord_summary_webhook_url: SecretStr | None = None
    discord_interesting_webhook_url: SecretStr | None = None
    discord_opportunities_webhook_url: SecretStr | None = None

    hn_poll_hours: int = Field(default=6, ge=1)
    hn_fetch_limit: int = Field(default=60, ge=5, le=200)
    hn_include_best: bool = False
    summary_top_n: int = Field(default=8, ge=1, le=10)
    interesting_top_n: int = Field(default=12, ge=1, le=20)
    opportunities_top_n: int = Field(default=5, ge=1, le=10)
    summary_topic_count: int = Field(default=5, ge=1, le=8)

    sqlite_path: str = "data/personal_agent.db"
    bot_startup_hn_run: bool = False

    llm_provider: str = "heuristic"
    llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_MODEL", "NEBIUS_MODEL"),
    )
    llm_base_url: str = Field(
        default="https://api.tokenfactory.us-central1.nebius.com/v1/",
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_BASE_URL", "NEBIUS_BASE_URL"),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_API_KEY", "NEBIUS_API_KEY"),
    )

    @property
    def effective_llm_model(self) -> str | None:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider.lower() == "nebius":
            return "moonshotai/Kimi-K2.5-fast"
        return None

    @property
    def llm_api_key_value(self) -> str | None:
        if self.llm_api_key is None:
            return None
        return self.llm_api_key.get_secret_value()

    @property
    def database_path(self) -> Path:
        return Path(self.sqlite_path)

    @property
    def discord_enabled(self) -> bool:
        return self.discord_bot_token is not None and bool(self.discord_bot_token.get_secret_value())

    @staticmethod
    def _secret_value(secret: SecretStr | None) -> str | None:
        if secret is None:
            return None
        return secret.get_secret_value()

    @property
    def discord_webhooks_enabled(self) -> bool:
        return any(self.channel_webhook_urls.values())

    @property
    def discord_publish_enabled(self) -> bool:
        return self.discord_enabled or self.discord_webhooks_enabled

    @property
    def channel_ids(self) -> dict[str, int | None]:
        return {
            "summary": self.discord_summary_channel_id,
            "interesting": self.discord_interesting_channel_id,
            "opportunities": self.discord_opportunities_channel_id,
        }

    @property
    def channel_webhook_urls(self) -> dict[str, str | None]:
        return {
            "summary": self._secret_value(self.discord_summary_webhook_url),
            "interesting": self._secret_value(self.discord_interesting_webhook_url),
            "opportunities": self._secret_value(self.discord_opportunities_webhook_url),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
