from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_agent.discord.bot import DISCORD_MESSAGE_CHAR_LIMIT, PersonalAgentDiscordBot, split_discord_message_content


class FakeChannel:
    def __init__(self) -> None:
        self.name = "hacker-news"
        self.guild = SimpleNamespace(name="Test Guild")
        self.sent_messages: list[str] = []

    async def send(self, message: str) -> None:
        self.sent_messages.append(message)


def test_split_discord_message_content_preserves_short_message() -> None:
    message = "Short Hacker News digest"

    assert split_discord_message_content(message) == [message]


def test_split_discord_message_content_splits_large_message_without_losing_content() -> None:
    message = "\n\n".join(f"Section {index}: " + ("detail " * 180) for index in range(1, 7))

    parts = split_discord_message_content(message, limit=500)

    assert len(parts) > 1
    assert "".join(parts) == message
    assert all(len(part) <= 500 for part in parts)


@pytest.mark.asyncio
async def test_send_digest_message_sends_multiple_chunks_for_long_content() -> None:
    bot = object.__new__(PersonalAgentDiscordBot)
    bot.settings = SimpleNamespace(channel_ids={"summary": 123})
    fake_channel = FakeChannel()
    bot.get_channel = lambda channel_id: fake_channel if channel_id == 123 else None

    message = "\n\n".join(f"Section {index}: " + ("detail " * 600) for index in range(1, 8))
    assert len(message) > DISCORD_MESSAGE_CHAR_LIMIT

    await PersonalAgentDiscordBot.send_digest_message(bot, "summary", message)

    assert len(fake_channel.sent_messages) > 1
    assert "".join(fake_channel.sent_messages) == message
    assert all(len(part) <= DISCORD_MESSAGE_CHAR_LIMIT for part in fake_channel.sent_messages)


def test_format_pi_status_message_includes_sandbox_details() -> None:
    message = PersonalAgentDiscordBot.format_pi_status_message(
        {
            "available": True,
            "default_provider": "openai",
            "default_model": "openai/gpt-5.4-mini",
            "sandbox_mode": "isolated_repo_clone",
            "repo_workflow_available": True,
            "workspace_root": "/tmp/personal-agent/pi",
        }
    )

    assert "openai / openai/gpt-5.4-mini" in message
    assert "isolated_repo_clone" in message
    assert "/tmp/personal-agent/pi" in message


def test_format_pr_review_message_targets_command_center_review() -> None:
    message = PersonalAgentDiscordBot.format_pr_review_message(
        pr_url="https://github.com/example/repo/pull/1",
        author_mention="@bhaves",
    )

    assert "@bhaves" in message
    assert "review this pull request in command-center" in message
    assert "https://github.com/example/repo/pull/1" in message
