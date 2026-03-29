from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_agent.agent.models import (
    AgentArtifact,
    AgentFollowUp,
    AgentResponse,
    AgentRuntimeContext,
)
from personal_agent.automation.models import PiToolExecution
from personal_agent.discord.bot import (
    DISCORD_MESSAGE_CHAR_LIMIT,
    PersonalAgentDiscordBot,
    split_discord_message_content,
)


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
            "cloud_agent_mode": "blaxel",
            "sandbox_mode": "blaxel_execution_sandbox",
            "blaxel_execution_sandbox_image": "personal-agent-pi-workspace-template",
            "blaxel_repo_sandbox_image": "personal-agent-pi-workspace-template",
            "repo_workflow_available": True,
            "workspace_root": "/tmp/personal-agent/pi",
        }
    )

    assert "openai / openai/gpt-5.4-mini" in message
    assert "blaxel" in message
    assert "personal-agent-pi-workspace-template" in message
    assert "/tmp/personal-agent/pi" in message


def test_format_pr_review_message_targets_command_center_review() -> None:
    message = PersonalAgentDiscordBot.format_pr_review_message(
        pr_url="https://github.com/example/repo/pull/1",
        author_mention="@bhaves",
    )

    assert "@bhaves" in message
    assert "review this pull request in command-center" in message
    assert "https://github.com/example/repo/pull/1" in message


def test_format_pi_task_result_message_includes_output() -> None:
    message = PersonalAgentDiscordBot.format_pi_task_result_message(
        SimpleNamespace(
            exit_code=0,
            stdout="10th fibonacci number is 55",
            stderr="",
        )
    )

    assert "**Pi**" in message
    assert "55" in message


def test_format_pi_task_result_message_strips_raw_tokens() -> None:
    message = PersonalAgentDiscordBot.format_pi_task_result_message(
        SimpleNamespace(
            exit_code=0,
            stdout='<|tool_calls_section_begin|> <|tool_call_begin|> call_1 <|tool_call_argument_begin|> {"command": "echo hello"} <|tool_call_end|> <|tool_calls_section_end|>',
            stderr="",
        )
    )

    assert "<|" not in message
    assert "**Pi**" in message


def test_format_pi_chat_messages_include_trace_and_followup_hint() -> None:
    messages = PersonalAgentDiscordBot.format_pi_chat_messages(
        AgentResponse(
            kind="chat",
            ok=True,
            final_text="The 10th Fibonacci number is 55.",
            session_id="discord-123-456",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="discord",
                duration_seconds=3.5,
                sandbox_mode="blaxel_execution_sandbox",
                sandbox_name="personal-agent-exec-123",
                sandbox_image="personal-agent-pi-workspace-template",
            ),
            tool_traces=[
                PiToolExecution(
                    tool_name="bash",
                    arguments={"command": 'node -e "console.log(55)"'},
                    output="55",
                    is_error=False,
                )
            ],
            followups=[
                AgentFollowUp(action="continue_session", label="Reply to continue"),
                AgentFollowUp(action="clear_session", label="Clear session"),
            ],
        )
    )

    combined = "\n".join(messages)
    assert "**Pi Response**" in combined
    assert "55" in combined
    assert "**Execution Summary**" in combined
    assert "**Tool Trace**" in combined
    assert "`bash` (ok)" in combined
    assert "personal-agent-pi-workspace-template" in combined
    assert "!code-reset" in combined


def test_format_pi_chat_messages_strip_raw_tokens() -> None:
    messages = PersonalAgentDiscordBot.format_pi_chat_messages(
        SimpleNamespace(
            exit_code=0,
            stdout="result",
            assistant_response='<|tool_calls_section_begin|> <|tool_call_begin|> call_1 <|tool_call_end|> <|tool_calls_section_end|>',
            duration_seconds=2.0,
            tool_traces=[],
        )
    )

    combined = "\n".join(messages)
    assert "<|" not in combined


def test_format_pi_chat_messages_show_error_traces() -> None:
    messages = PersonalAgentDiscordBot.format_pi_chat_messages(
        SimpleNamespace(
            exit_code=0,
            stdout="Retried successfully.",
            assistant_response="Retried successfully.",
            duration_seconds=5.0,
            tool_traces=[
                SimpleNamespace(
                    tool_name="bash",
                    arguments={"command": "python3 -c 'print(1)'"},
                    output="sh: python3: not found",
                    is_error=True,
                ),
                SimpleNamespace(
                    tool_name="bash",
                    arguments={"command": "node -e 'console.log(1)'"},
                    output="1",
                    is_error=False,
                ),
            ],
        )
    )

    combined = "\n".join(messages)
    assert "`bash` (error)" in combined
    assert "`bash` (ok)" in combined
    assert "python3" in combined
    assert "node" in combined


def test_format_pi_repo_result_message_marks_push_pending() -> None:
    message = PersonalAgentDiscordBot.format_pi_repo_result_message(
        AgentResponse(
            kind="repo_prepare",
            ok=True,
            final_text="Prepared repository changes and committed them in sandbox. Push is waiting for explicit approval.",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="discord",
                sandbox_mode="per_repo_persistent_sandbox",
            ),
            artifacts=[
                AgentArtifact(kind="workspace", label="Workspace", value="pi-repo-123"),
                AgentArtifact(kind="branch", label="Branch", value="personal-agent/test"),
            ],
            followups=[
                AgentFollowUp(
                    action="approve_repo_push",
                    label="Approve repository push",
                    data={"workspace_id": "pi-repo-123"},
                )
            ],
        )
    )

    assert "Push is waiting for explicit approval." in message
    assert "Runtime:" in message


def test_format_repo_push_instruction_message_mentions_repo_push_command() -> None:
    message = PersonalAgentDiscordBot.format_repo_push_instruction_message(
        workspace_id="pi-repo-123",
        author_mention="@bhaves",
    )

    assert "@bhaves" in message
    assert "!repo-push pi-repo-123" in message


def test_format_pi_repo_push_result_message_with_pr_url() -> None:
    message = PersonalAgentDiscordBot.format_pi_repo_push_result_message(
        AgentResponse(
            kind="repo_push",
            ok=True,
            final_text="Push approved and completed. Pull request created: https://github.com/example/repo/pull/1",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="discord",
                sandbox_mode="per_repo_persistent_sandbox",
            ),
            artifacts=[
                AgentArtifact(kind="workspace", label="Workspace", value="pi-repo-123"),
                AgentArtifact(
                    kind="pull_request",
                    label="Pull request",
                    value="https://github.com/example/repo/pull/1",
                    url="https://github.com/example/repo/pull/1",
                ),
            ],
        )
    )

    assert "Push approved and completed" in message
    assert "https://github.com/example/repo/pull/1" in message


def test_format_followup_messages_maps_transport_neutral_actions() -> None:
    messages = PersonalAgentDiscordBot.format_followup_messages(
        AgentResponse(
            kind="repo_prepare",
            ok=True,
            final_text="Prepared repository changes.",
            followups=[
                AgentFollowUp(
                    action="approve_repo_push",
                    label="Approve repository push",
                    data={"workspace_id": "pi-repo-123"},
                ),
                AgentFollowUp(
                    action="review_pull_request",
                    label="Review pull request",
                    data={"url": "https://github.com/example/repo/pull/1"},
                ),
            ],
        ),
        author_mention="@bhaves",
    )

    assert any("!repo-push pi-repo-123" in message for message in messages)
    assert any("https://github.com/example/repo/pull/1" in message for message in messages)
