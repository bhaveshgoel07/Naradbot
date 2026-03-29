from __future__ import annotations

import json

import pytest

from personal_agent.automation.models import (
    PiRepositoryPushRequest,
    PiRepositoryPushResult,
    PiRepositoryTaskRequest,
    PiRepositoryTaskResult,
    PiTaskRequest,
    PiTaskResult,
)
from personal_agent.automation.pi_agent import PiCodingAgentService
from personal_agent.config.settings import Settings


class FakeSandboxService:
    def __init__(self) -> None:
        self.ensure_calls = 0

    async def ensure_orchestrator_sandbox(self):
        self.ensure_calls += 1
        return type("SandboxHandle", (), {"name": "pi-orchestrator"})()


@pytest.mark.asyncio
async def test_run_task_uses_blaxel_execution_path_when_enabled(monkeypatch) -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    sandbox_service = FakeSandboxService()
    service = PiCodingAgentService(settings=settings, sandbox_service=sandbox_service)

    async def fake_run_task_in_execution_sandbox(
        self, request: PiTaskRequest
    ) -> PiTaskResult:
        assert request.prompt == "write a fibonacci script"
        return PiTaskResult(
            available=True,
            command=["pi", "-p", request.prompt],
            exit_code=0,
            stdout="done",
            stderr="",
            duration_seconds=1.2,
        )

    monkeypatch.setattr(
        PiCodingAgentService,
        "_run_task_in_execution_sandbox",
        fake_run_task_in_execution_sandbox,
    )

    result = await service.run_task(PiTaskRequest(prompt="write a fibonacci script"))

    assert sandbox_service.ensure_calls == 1
    assert result.stdout == "done"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_repository_task_uses_blaxel_repo_path_when_enabled(monkeypatch) -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    service = PiCodingAgentService(
        settings=settings,
        sandbox_service=FakeSandboxService(),
    )

    async def fake_run_repository_task_in_blaxel(
        self, request: PiRepositoryTaskRequest
    ) -> PiRepositoryTaskResult:
        assert request.repo_url == "https://github.com/example/repo"
        return PiRepositoryTaskResult(
            available=True,
            command=["pi", "-p", request.prompt],
            exit_code=0,
            stdout="prepared",
            stderr="",
            duration_seconds=2.1,
            sandbox_mode="per_repo_persistent_sandbox",
            workspace_dir="/workspace/workspaces/ws-1",
            repo_dir="/workspace/workspaces/ws-1/repo",
            repo_url=request.repo_url,
            workspace_id="repo-sandbox__ws-1",
        )

    monkeypatch.setattr(
        PiCodingAgentService,
        "_run_repository_task_in_blaxel",
        fake_run_repository_task_in_blaxel,
    )

    result = await service.run_repository_task(
        PiRepositoryTaskRequest(
            repo_url="https://github.com/example/repo",
            prompt="update the README",
        )
    )

    assert result.sandbox_mode == "per_repo_persistent_sandbox"
    assert result.workspace_id == "repo-sandbox__ws-1"


@pytest.mark.asyncio
async def test_approve_repository_push_uses_blaxel_repo_path_when_enabled(monkeypatch) -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    service = PiCodingAgentService(
        settings=settings,
        sandbox_service=FakeSandboxService(),
    )

    async def fake_approve_repository_push_in_blaxel(
        self, request: PiRepositoryPushRequest
    ) -> PiRepositoryPushResult:
        assert request.workspace_id == "repo-sandbox__ws-1"
        return PiRepositoryPushResult(
            available=True,
            command=["git", "push", "-u", "origin", "branch"],
            exit_code=0,
            stdout="pushed",
            stderr="",
            duration_seconds=3.4,
            sandbox_mode="per_repo_persistent_sandbox",
            workspace_id=request.workspace_id,
            workspace_dir="/workspace/workspaces/ws-1",
            repo_dir="/workspace/workspaces/ws-1/repo",
            repo_url="https://github.com/example/repo",
            branch_name="branch",
            base_branch="main",
            pr_url="https://github.com/example/repo/pull/12",
            review_required=True,
        )

    monkeypatch.setattr(
        PiCodingAgentService,
        "_approve_repository_push_in_blaxel",
        fake_approve_repository_push_in_blaxel,
    )

    result = await service.approve_repository_push(
        PiRepositoryPushRequest(workspace_id="repo-sandbox__ws-1")
    )

    assert result.sandbox_mode == "per_repo_persistent_sandbox"
    assert result.pr_url == "https://github.com/example/repo/pull/12"


def test_nebius_provider_config_declares_text_input() -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    service = PiCodingAgentService(settings=settings)

    config = service._nebius_provider_config("moonshotai/Kimi-K2.5-fast")
    models = config["providers"]["nebius"]["models"]
    compat = config["providers"]["nebius"]["compat"]

    assert models == [
        {
            "id": "moonshotai/Kimi-K2.5-fast",
            "name": "moonshotai/Kimi-K2.5-fast",
            "input": ["text"],
            "reasoning": True,
        }
    ]
    assert compat["supportsDeveloperRole"] is False
    assert compat["supportsReasoningEffort"] is False
    assert compat["supportsStore"] is False
    assert compat["supportsUsageInStreaming"] is False
    assert compat["maxTokensField"] == "max_tokens"


def test_merged_error_output_keeps_real_failure_details() -> None:
    merged = PiCodingAgentService._merged_error_output(
        stdout="Failed to process the request",
        stderr="npm notice update available",
        logs="npm notice update available\nFailed to process the request\nstack trace",
    )

    assert "Failed to process the request" in merged
    assert "stack trace" in merged


def test_build_command_uses_json_mode_and_session_path() -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    service = PiCodingAgentService(settings=settings)

    command = service._build_command(
        PiTaskRequest(
            prompt="write hello world to console",
            session_id="discord-1-2",
            structured_output=True,
        ),
        output_format="json",
        session_path="/tmp/personal-agent/session.jsonl",
    )

    assert "--mode" in command
    assert "json" in command
    assert "--session" in command
    assert "/tmp/personal-agent/session.jsonl" in command
    assert "--no-session" not in command
    assert "--append-system-prompt" in command
    system_prompt = command[command.index("--append-system-prompt") + 1]
    assert "personal-agent-pi-workspace-template" in system_prompt
    assert "python3" in system_prompt


def test_strip_raw_llm_tokens_removes_special_tokens() -> None:
    raw = (
        '<|tool_calls_section_begin|> <|tool_call_begin|> call_1 '
        '<|tool_call_argument_begin|> {"command": "echo hello"} '
        '<|tool_call_end|> <|tool_calls_section_end|>'
    )
    result = PiCodingAgentService._strip_raw_llm_tokens(raw)

    assert "<|" not in result
    assert "call_1" not in result
    assert '{"command": "echo hello"}' in result


def test_strip_raw_llm_tokens_preserves_normal_text() -> None:
    normal = "The 10th Fibonacci number is 55."
    assert PiCodingAgentService._strip_raw_llm_tokens(normal) == normal


def test_strip_raw_llm_tokens_handles_empty_string() -> None:
    assert PiCodingAgentService._strip_raw_llm_tokens("") == ""


def test_parse_pi_json_output_falls_back_to_tool_result_when_final_text_missing() -> None:
    settings = Settings(environment="test", sqlite_path="data/test_pi_agent.db")
    service = PiCodingAgentService(settings=settings)

    payload = "\n".join(
        [
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call_0",
                    "toolName": "bash",
                    "args": {"command": "python3 -c 'print(34)'"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "call_0",
                    "toolName": "bash",
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "10th Fibonacci number (1-indexed): 34",
                            }
                        ]
                    },
                    "isError": False,
                }
            ),
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call_0",
                                    "name": "bash",
                                    "arguments": {"command": "python3 -c 'print(34)'"},
                                }
                            ],
                        },
                        {
                            "role": "toolResult",
                            "toolCallId": "call_0",
                            "toolName": "bash",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "10th Fibonacci number (1-indexed): 34",
                                }
                            ],
                            "isError": False,
                        },
                        {
                            "role": "assistant",
                            "content": [],
                        },
                    ],
                }
            ),
        ]
    )

    parsed = service._parse_pi_json_output(payload)

    assert parsed is not None
    assert parsed.assistant_response == ""
    assert parsed.primary_output == "10th Fibonacci number (1-indexed): 34"
    assert len(parsed.tool_traces) == 1
    assert parsed.tool_traces[0].tool_name == "bash"
    assert parsed.tool_traces[0].arguments == {"command": "python3 -c 'print(34)'"}
    assert parsed.tool_traces[0].output == "10th Fibonacci number (1-indexed): 34"
