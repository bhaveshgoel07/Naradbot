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

    api_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("PERSONAL_AGENT_API_HOST", "HOST"),
    )
    api_port: int = Field(
        default=8000, validation_alias=AliasChoices("PERSONAL_AGENT_API_PORT", "PORT")
    )

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
    summary_concurrency_limit: int = Field(default=6, ge=1, le=20)
    story_analysis_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_STORY_ANALYSIS_ENABLED",
            "PERSONAL_AGENT_SMALL_LLM_ENABLED",
        ),
    )
    story_analysis_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_STORY_ANALYSIS_MODEL",
            "PERSONAL_AGENT_SMALL_LLM_MODEL",
        ),
    )
    story_analysis_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_STORY_ANALYSIS_BASE_URL",
            "PERSONAL_AGENT_SMALL_LLM_BASE_URL",
        ),
    )
    story_analysis_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_STORY_ANALYSIS_API_KEY",
            "PERSONAL_AGENT_SMALL_LLM_API_KEY",
        ),
    )
    story_analysis_concurrency_limit: int = Field(default=4, ge=1, le=20)
    story_analysis_verify_links: bool = True
    story_analysis_link_timeout_seconds: int = Field(default=20, ge=5, le=60)
    story_analysis_link_char_limit: int = Field(default=6000, ge=500, le=20000)

    opportunity_embedding_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_ENABLED",
            "NEBIUS_EMBEDDING_ENABLED",
        ),
    )
    opportunity_embedding_model: str = Field(
        default="Qwen/Qwen3-Embedding-8B",
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_MODEL", "NEBIUS_EMBEDDING_MODEL"
        ),
    )
    opportunity_embedding_base_url: str = Field(
        default="https://api.tokenfactory.nebius.com/v1/",
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_BASE_URL", "NEBIUS_EMBEDDING_BASE_URL"
        ),
    )
    opportunity_embedding_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_API_KEY", "NEBIUS_API_KEY"
        ),
    )
    opportunity_hiring_keywords: tuple[str, ...] = (
        "hiring",
        "job",
        "jobs",
        "career",
        "contract",
        "freelance",
        "founding engineer",
        "internship",
        "intern",
        "remote",
        "who is hiring",
        "who wants to be hired",
    )
    opportunity_min_similarity: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_OPPORTUNITY_MIN_SIMILARITY",
            "NEBIUS_EMBEDDING_MIN_SIMILARITY",
        ),
    )
    opportunity_min_margin: float = Field(default=0.03, ge=0.0, le=1.0)
    opportunity_job_post_queries: tuple[str, ...] = (
        "A real job post with an open role and a request for candidates to apply.",
        "A hiring page describing a specific engineering role, team, location, or compensation.",
        "A contract, freelance, internship, or full-time role announcement with application intent.",
    )
    opportunity_non_job_queries: tuple[str, ...] = (
        "A discussion about hiring, careers, layoffs, or recruiting without a concrete opening.",
        "News or commentary about jobs rather than a job application page.",
        "A general conversation thread about work, careers, or startups without a direct role to apply for.",
    )

    sqlite_path: str = "data/personal_agent.db"
    bot_startup_hn_run: bool = False

    llm_provider: str = "heuristic"
    llm_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_MODEL", "NEBIUS_MODEL"),
    )
    llm_base_url: str = Field(
        default="https://api.tokenfactory.nebius.com/v1/",
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_BASE_URL", "NEBIUS_BASE_URL"),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PERSONAL_AGENT_LLM_API_KEY", "NEBIUS_API_KEY"),
    )
    pi_command: str = "npx -y @mariozechner/pi-coding-agent"
    pi_default_tools: tuple[str, ...] = (
        "read",
        "bash",
        "edit",
        "write",
        "grep",
        "find",
        "ls",
    )
    pi_provider: str | None = "nebius"
    pi_model: str | None = "moonshotai/Kimi-K2.5-fast"
    pi_base_url: str | None = "https://api.tokenfactory.us-central1.nebius.com/v1/"
    pi_api_key: SecretStr | None = None
    pi_default_thinking: str | None = "low"
    pi_no_session: bool = True
    pi_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    pi_workspace_root: str = "/tmp/personal-agent/pi"
    pi_github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PERSONAL_AGENT_PI_GITHUB_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        ),
    )
    pi_git_author_name: str = "personal-agent"
    pi_git_author_email: str = "personal-agent@local.invalid"
    blaxel_sandboxes_enabled: bool = True
    blaxel_region: str = Field(
        default="us-pdx-1",
        validation_alias=AliasChoices("PERSONAL_AGENT_BLAXEL_REGION", "BL_REGION"),
    )
    blaxel_orchestrator_sandbox_name: str = "personal-agent-pi-orchestrator"
    blaxel_orchestrator_sandbox_image: str = "personal-agent-pi-orchestrator-template"
    blaxel_orchestrator_sandbox_memory: int = Field(default=4096, ge=512, le=32768)
    blaxel_orchestrator_sandbox_ttl: str | None = None
    blaxel_orchestrator_sandbox_idle_ttl: str | None = "30d"
    blaxel_orchestrator_volume_name: str | None = None
    blaxel_orchestrator_volume_mount_path: str | None = "/workspace"
    blaxel_orchestrator_workspace_root: str = "/workspace"
    blaxel_execution_sandbox_prefix: str = "personal-agent-exec"
    blaxel_execution_sandbox_image: str = "personal-agent-pi-workspace-template"
    blaxel_execution_sandbox_memory: int = Field(default=2048, ge=512, le=32768)
    blaxel_execution_sandbox_ttl: str | None = "24h"
    blaxel_execution_sandbox_idle_ttl: str | None = "24h"
    blaxel_execution_volume_name: str | None = None
    blaxel_execution_volume_mount_path: str | None = "/workspace"
    blaxel_execution_workspace_root: str = "/workspace"
    blaxel_repo_sandbox_prefix: str = "personal-agent-repo"
    blaxel_repo_sandbox_image: str = "personal-agent-pi-workspace-template"
    blaxel_repo_sandbox_memory: int = Field(default=4096, ge=512, le=32768)
    blaxel_repo_sandbox_ttl: str | None = None
    blaxel_repo_sandbox_idle_ttl: str | None = "14d"
    blaxel_repo_volume_name: str | None = None
    blaxel_repo_volume_mount_path: str | None = "/workspace"
    blaxel_repo_workspace_root: str = "/workspace"
    blaxel_computer_use_sandbox_name: str = "personal-agent-computer-use"
    blaxel_computer_use_sandbox_image: str = "personal-agent-computer-use-template"
    blaxel_computer_use_sandbox_memory: int = Field(default=4096, ge=512, le=32768)
    blaxel_computer_use_sandbox_ttl: str | None = None
    blaxel_computer_use_sandbox_idle_ttl: str | None = "7d"
    blaxel_computer_use_volume_name: str | None = None
    blaxel_computer_use_volume_mount_path: str | None = "/workspace"
    blaxel_computer_use_workspace_root: str = "/workspace"
    blaxel_computer_use_preview_port: int | None = Field(default=3000, ge=1, le=65535)
    candidate_full_name: str | None = None
    candidate_email: str | None = None
    candidate_phone: str | None = None
    candidate_location: str | None = None
    candidate_linkedin_url: str | None = None
    candidate_github_url: str | None = None
    candidate_portfolio_url: str | None = None
    candidate_resume_path: str | None = None
    candidate_cover_letter_path: str | None = None
    candidate_extra_notes: str | None = None
    computer_use_command: str | None = None
    job_application_timeout_seconds: int = Field(default=1200, ge=30, le=7200)

    @property
    def effective_llm_model(self) -> str | None:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider.lower() == "nebius":
            return "NousResearch/Hermes-4-70B"
        return None

    @property
    def llm_api_key_value(self) -> str | None:
        if self.llm_api_key is None:
            return None
        return self.llm_api_key.get_secret_value()

    @property
    def story_analysis_model_value(self) -> str | None:
        return self.story_analysis_model or self.effective_llm_model

    @property
    def story_analysis_base_url_value(self) -> str:
        return self.story_analysis_base_url or self.llm_base_url

    @property
    def story_analysis_api_key_value(self) -> str | None:
        if self.story_analysis_api_key is not None:
            return self.story_analysis_api_key.get_secret_value()
        return self.llm_api_key_value

    @property
    def summary_model_value(self) -> str | None:
        if self.llm_model:
            return self.llm_model
        if self.story_analysis_model:
            return self.story_analysis_model
        return self.effective_llm_model

    @property
    def summary_base_url_value(self) -> str:
        return self.story_analysis_base_url or self.llm_base_url

    @property
    def summary_api_key_value(self) -> str | None:
        if self.llm_api_key is not None:
            return self.llm_api_key.get_secret_value()
        if self.story_analysis_api_key is not None:
            return self.story_analysis_api_key.get_secret_value()
        return None

    @property
    def opportunity_embedding_api_key_value(self) -> str | None:
        if self.opportunity_embedding_api_key is not None:
            return self.opportunity_embedding_api_key.get_secret_value()
        return self.llm_api_key_value

    @property
    def pi_provider_value(self) -> str | None:
        return self.pi_provider

    @property
    def pi_model_value(self) -> str | None:
        return self.pi_model or self.llm_model

    @property
    def pi_base_url_value(self) -> str | None:
        return self.pi_base_url or self.llm_base_url

    @property
    def pi_api_key_value(self) -> str | None:
        if self.pi_api_key is None:
            if self.pi_provider_value == "nebius":
                return self.llm_api_key_value
            return None
        return self.pi_api_key.get_secret_value()

    @property
    def pi_github_token_value(self) -> str | None:
        if self.pi_github_token is None:
            return None
        return self.pi_github_token.get_secret_value()

    @property
    def database_path(self) -> Path:
        return Path(self.sqlite_path)

    @property
    def pi_workspace_root_path(self) -> Path:
        return Path(self.pi_workspace_root)

    @property
    def candidate_resume_file(self) -> Path | None:
        if not self.candidate_resume_path:
            return None
        return Path(self.candidate_resume_path)

    @property
    def candidate_cover_letter_file(self) -> Path | None:
        if not self.candidate_cover_letter_path:
            return None
        return Path(self.candidate_cover_letter_path)

    @property
    def discord_enabled(self) -> bool:
        return self.discord_bot_token is not None and bool(
            self.discord_bot_token.get_secret_value()
        )

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
