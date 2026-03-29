from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_agent.agent.models import AgentMessageRequest, AgentRepositoryRequest
from personal_agent.agent.service import AgentOrchestratorService
from personal_agent.automation.models import (
    PiRepositoryTaskResult,
    PiTaskResult,
    PiToolExecution,
)
from personal_agent.config.settings import Settings


@pytest.mark.asyncio
async def test_handle_message_derives_session_and_transport_prompt() -> None:
    captured_request = None

    async def fake_run_task(request):
        nonlocal captured_request
        captured_request = request
        return PiTaskResult(
            available=True,
            command=["pi", "-p", request.prompt],
            exit_code=0,
            stdout="Hello from Pi.",
            stderr="",
            duration_seconds=1.5,
            session_id=request.session_id,
            assistant_response="Hello from Pi.",
            tool_traces=[
                PiToolExecution(
                    tool_name="bash",
                    arguments={"command": "echo hello"},
                    output="hello",
                    is_error=False,
                )
            ],
            sandbox_mode="blaxel_execution_sandbox",
            sandbox_name="personal-agent-exec-123",
            sandbox_image="personal-agent-pi-workspace-template",
        )

    orchestrator = AgentOrchestratorService(
        settings=Settings(environment="test", sqlite_path="data/test_agent_orchestrator.db"),
        pi_agent=SimpleNamespace(
            run_task=fake_run_task,
            clear_task_session=lambda session_id: None,
            status=lambda: {},
        ),
    )

    response = await orchestrator.handle_message(
        AgentMessageRequest(
            prompt="Say hello",
            transport="discord",
            conversation_id="123",
            actor_id="456",
        )
    )

    assert captured_request is not None
    assert captured_request.session_id == "discord-123-456"
    assert "responding through the discord interface" in captured_request.append_system_prompt.lower()
    assert response.ok is True
    assert response.session_id == "discord-123-456"
    assert response.runtime.sandbox_mode == "blaxel_execution_sandbox"
    assert {followup.action for followup in response.followups} == {
        "continue_session",
        "clear_session",
    }


@pytest.mark.asyncio
async def test_prepare_repository_emits_artifacts_and_followups() -> None:
    async def fake_run_repository_task(request):
        assert request.repo_url == "https://github.com/example/repo"
        return PiRepositoryTaskResult(
            available=True,
            command=["pi", "-p", request.prompt],
            exit_code=0,
            stdout="Prepared repo changes.",
            stderr="",
            duration_seconds=4.2,
            sandbox_mode="per_repo_persistent_sandbox",
            workspace_dir="/workspace/workspaces/ws-1",
            repo_dir="/workspace/workspaces/ws-1/repo",
            repo_url=request.repo_url,
            sandbox_name="repo-sandbox",
            sandbox_image="personal-agent-pi-workspace-template",
            workspace_id="repo-sandbox__ws-1",
            branch_name="personal-agent/test-branch",
            commit_sha="abc123",
            changes_detected=True,
            push_pending=True,
        )

    orchestrator = AgentOrchestratorService(
        settings=Settings(environment="test", sqlite_path="data/test_agent_orchestrator.db"),
        pi_agent=SimpleNamespace(
            run_repository_task=fake_run_repository_task,
            clear_task_session=lambda session_id: None,
            status=lambda: {},
        ),
    )

    response = await orchestrator.prepare_repository(
        AgentRepositoryRequest(
            repo_url="https://github.com/example/repo",
            prompt="Fix the test failure",
            transport="api",
        )
    )

    assert response.ok is True
    assert response.kind == "repo_prepare"
    assert response.runtime.sandbox_mode == "per_repo_persistent_sandbox"
    artifact_kinds = {artifact.kind for artifact in response.artifacts}
    assert {"workspace", "branch", "commit"} <= artifact_kinds
    assert {followup.action for followup in response.followups} == {"approve_repo_push"}
