from __future__ import annotations

from personal_agent.config.settings import Settings


def test_settings_support_bare_host_and_port_aliases() -> None:
    settings = Settings.model_validate(
        {
            "HOST": "127.0.0.1",
            "PORT": 9000,
        }
    )

    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9000


def test_settings_expose_channel_webhook_urls() -> None:
    settings = Settings(
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
