from __future__ import annotations

from pathlib import Path

from pydantic_settings import SettingsConfigDict

from personal_agent.config.settings import Settings


class SettingsForTest(Settings):
    """
    Isolated Settings subclass for deterministic tests.

    - Does not read `.env`
    - Ignores ambient OS environment variables
    """

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="PERSONAL_AGENT_",
        extra="ignore",
        populate_by_name=True,
    )


def test_settings_support_bare_host_and_port_aliases() -> None:
    settings = SettingsForTest.model_validate(
        {
            "HOST": "127.0.0.1",
            "PORT": 9000,
        }
    )

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9000


def test_settings_expose_channel_webhook_urls() -> None:
    settings = SettingsForTest(
        discord_summary_webhook_url="https://discord.com/api/webhooks/summary",
        discord_interesting_webhook_url="https://discord.com/api/webhooks/interesting",
    )

    assert settings.discord_webhooks_enabled is True
    assert settings.discord_publish_enabled is True
    assert settings.channel_webhook_urls == {
        "summary": "https://discord.com/api/webhooks/summary",
        "interesting": "https://discord.com/api/webhooks/interesting",
        "opportunities": None,
    }


def test_settings_expose_opportunity_embedding_defaults() -> None:
    settings = SettingsForTest()

    assert settings.opportunity_embedding_enabled is True
    assert settings.opportunity_embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert (
        settings.opportunity_embedding_base_url
        == "https://api.tokenfactory.nebius.com/v1/"
    )
    assert settings.opportunity_embedding_api_key_value is None
    assert settings.opportunity_hiring_keywords == (
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
    assert settings.opportunity_min_similarity == 0.45


def test_settings_support_opportunity_embedding_aliases_and_api_key_value() -> None:
    settings = SettingsForTest.model_validate(
        {
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_ENABLED": True,
            "NEBIUS_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B",
            "NEBIUS_EMBEDDING_BASE_URL": "https://api.tokenfactory.nebius.com/v1/",
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_API_KEY": "test-nebius-key",
            "PERSONAL_AGENT_OPPORTUNITY_MIN_SIMILARITY": 0.62,
        }
    )

    assert settings.opportunity_embedding_enabled is True
    assert settings.opportunity_embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert (
        settings.opportunity_embedding_base_url
        == "https://api.tokenfactory.nebius.com/v1/"
    )
    assert settings.opportunity_embedding_api_key_value == "test-nebius-key"
    assert settings.opportunity_min_similarity == 0.62


def test_settings_opportunity_embedding_api_key_falls_back_to_llm_api_key() -> None:
    settings = SettingsForTest.model_validate(
        {
            "PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_ENABLED": True,
            "PERSONAL_AGENT_LLM_API_KEY": "test-llm-key",
        }
    )

    assert settings.llm_api_key_value == "test-llm-key"
    assert settings.opportunity_embedding_api_key_value == "test-llm-key"


def test_settings_expose_story_analysis_and_pi_defaults() -> None:
    settings = SettingsForTest()

    assert settings.story_analysis_enabled is False
    assert settings.story_analysis_model_value is None
    assert settings.story_analysis_base_url_value == settings.llm_base_url
    assert settings.story_analysis_api_key_value is None
    assert settings.pi_command == "npx -y @mariozechner/pi-coding-agent"
    assert settings.pi_provider_value == "nebius"
    assert settings.pi_model_value == "moonshotai/Kimi-K2.5-fast"
    assert (
        settings.pi_base_url_value
        == "https://api.tokenfactory.us-central1.nebius.com/v1/"
    )
    assert settings.pi_api_key_value is None
    assert settings.pi_default_tools == (
        "read",
        "bash",
        "edit",
        "write",
        "grep",
        "find",
        "ls",
    )
    assert settings.pi_default_thinking == "low"
    assert settings.pi_workspace_root_path == Path("/tmp/personal-agent/pi")
    assert settings.blaxel_region == "us-pdx-1"
    assert settings.blaxel_orchestrator_sandbox_name == "personal-agent-pi-orchestrator"
    assert (
        settings.blaxel_orchestrator_sandbox_image
        == "personal-agent-pi-orchestrator-template"
    )
    assert settings.blaxel_execution_sandbox_image == "personal-agent-pi-workspace-template"
    assert settings.blaxel_repo_sandbox_image == "personal-agent-pi-workspace-template"
    assert settings.blaxel_computer_use_sandbox_name == "personal-agent-computer-use"
    assert settings.blaxel_computer_use_sandbox_image == "personal-agent-computer-use-template"


def test_settings_support_blaxel_region_alias() -> None:
    settings = SettingsForTest.model_validate(
        {
            "BL_REGION": "eu-lon-1",
        }
    )

    assert settings.blaxel_region == "eu-lon-1"


def test_settings_pi_api_key_falls_back_to_llm_api_key() -> None:
    settings = SettingsForTest.model_validate(
        {
            "PERSONAL_AGENT_LLM_API_KEY": "test-nebius-key",
        }
    )

    assert settings.pi_api_key_value == "test-nebius-key"


def test_settings_story_analysis_api_key_falls_back_to_llm_api_key() -> None:
    settings = SettingsForTest.model_validate(
        {
            "PERSONAL_AGENT_STORY_ANALYSIS_ENABLED": True,
            "PERSONAL_AGENT_LLM_API_KEY": "test-llm-key",
            "PERSONAL_AGENT_SMALL_LLM_MODEL": "NousResearch/Hermes-4-70B",
        }
    )

    assert settings.story_analysis_enabled is True
    assert settings.story_analysis_model_value == "NousResearch/Hermes-4-70B"
    assert settings.story_analysis_api_key_value == "test-llm-key"


def test_settings_summary_model_falls_back_to_story_analysis_model() -> None:
    settings = SettingsForTest.model_validate(
        {
            "PERSONAL_AGENT_SMALL_LLM_MODEL": "NousResearch/Hermes-4-70B",
            "PERSONAL_AGENT_STORY_ANALYSIS_API_KEY": "test-nebius-key",
        }
    )

    assert settings.summary_model_value == "NousResearch/Hermes-4-70B"
    assert settings.summary_api_key_value == "test-nebius-key"
