from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from personal_agent.agent.models import (
    AgentArtifact,
    AgentResponse,
    AgentRuntimeContext,
)
from personal_agent.app import create_app
from personal_agent.automation.models import PiToolExecution
from personal_agent.config.settings import Settings


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_blaxel_inference_endpoint_forwards_inputs_to_pi() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    async def fake_handle_message(request):
        assert request.prompt == "Summarize the repository."
        assert request.provider is None
        assert request.session_id == "cloud-session-1"
        assert request.transport == "api"
        return AgentResponse(
            kind="chat",
            ok=True,
            final_text="Repository summary",
            session_id="cloud-session-1",
            runtime=AgentRuntimeContext(
                transport="api",
                duration_seconds=1.2,
            ),
        )

    app.state.container.agent_orchestrator_service = SimpleNamespace(
        handle_message=fake_handle_message
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/",
            json={
                "inputs": "Summarize the repository.",
                "session_id": "cloud-session-1",
            },
        )

    assert response.status_code == 200
    assert response.text == "Repository summary"


@pytest.mark.asyncio
async def test_pi_run_endpoint_returns_transparent_metadata() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    async def fake_handle_message(request):
        assert request.prompt == "Fix the bug"
        assert request.session_id == "sess-1"
        assert request.transport == "api"
        return AgentResponse(
            kind="chat",
            ok=True,
            final_text="Bug fixed.",
            session_id="sess-1",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="api",
                sandbox_mode="blaxel_execution_sandbox",
                sandbox_name="personal-agent-exec-123",
                sandbox_image="personal-agent-pi-workspace-template",
                duration_seconds=2.4,
            ),
            tool_traces=[
                PiToolExecution(
                    tool_name="bash",
                    tool_call_id="call-1",
                    arguments={"command": "pytest tests/test_bug.py"},
                    output="1 passed",
                    is_error=False,
                )
            ],
        )

    app.state.container.agent_orchestrator_service = SimpleNamespace(
        handle_message=fake_handle_message
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/pi/run",
            json={"prompt": "Fix the bug", "session_id": "sess-1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_text"] == "Bug fixed."
    assert payload["session_id"] == "sess-1"
    assert payload["runtime"]["sandbox_mode"] == "blaxel_execution_sandbox"
    assert payload["runtime"]["sandbox_image"] == "personal-agent-pi-workspace-template"
    assert payload["tool_traces"][0]["tool_name"] == "bash"


@pytest.mark.asyncio
async def test_hacker_news_graph_mermaid_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workflows/hacker-news/graph?format=mermaid")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vnd.mermaid")
    assert "graph TD;" in response.text
    assert "fetch_story_sources" in response.text
    assert "prepare_shared_scores" in response.text
    assert "run_editorial_arm" in response.text
    assert "run_opportunity_arm" in response.text
    assert "merge_story_scores" in response.text


@pytest.mark.asyncio
async def test_hacker_news_graph_ascii_failure_returns_503() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workflows/hacker-news/graph?format=ascii")

    assert response.status_code == 503
    assert response.json()["detail"].startswith("Unable to render ASCII graph:")


@pytest.mark.asyncio
async def test_hacker_news_graph_png_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    def _raise() -> bytes:
        raise ImportError("Install pygraphviz")

    app.state.container.hn_workflow.draw_png = _raise

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/workflows/hacker-news/graph?format=png")

    assert response.status_code == 503
    assert response.json()["detail"] == "Unable to render PNG graph: Install pygraphviz"


@pytest.mark.asyncio
async def test_pi_status_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    app.state.container.agent_orchestrator_service = SimpleNamespace(
        status=lambda: {
            "configured_command": ["pi", "-p"],
            "resolved_binary": "/usr/bin/pi",
            "available": True,
            "default_tools": ["read", "bash"],
            "default_model": "openai/gpt-5.4-mini",
            "default_provider": "openai",
        }
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/agents/pi/status")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["configured_command"] == ["pi", "-p"]


@pytest.mark.asyncio
async def test_pi_repo_run_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    async def fake_prepare_repository(request):
        assert request.repo_url == "https://github.com/example/repo"
        assert request.prompt == "Fix the failing test and open a PR."
        assert request.allow_push is False
        return AgentResponse(
            kind="repo_prepare",
            ok=True,
            final_text="Prepared repository changes and opened a pull request: https://github.com/example/repo/pull/1",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="api",
                sandbox_mode="per_repo_persistent_sandbox",
                sandbox_name="repo-sandbox",
                sandbox_image="personal-agent-pi-workspace-template",
                duration_seconds=12.5,
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

    app.state.container.agent_orchestrator_service = SimpleNamespace(
        prepare_repository=fake_prepare_repository
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/pi/repos/run",
            json={
                "repo_url": "https://github.com/example/repo",
                "prompt": "Fix the failing test and open a PR.",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "repo_prepare"
    assert payload["runtime"]["sandbox_mode"] == "per_repo_persistent_sandbox"
    assert payload["artifacts"][0]["value"] == "pi-repo-123"
    assert payload["artifacts"][1]["url"] == "https://github.com/example/repo/pull/1"


@pytest.mark.asyncio
async def test_pi_repo_push_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    async def fake_approve_repository_push(request):
        assert request.workspace_id == "pi-repo-123"
        return AgentResponse(
            kind="repo_push",
            ok=True,
            final_text="Push approved and completed. Pull request created: https://github.com/example/repo/pull/2",
            exit_code=0,
            runtime=AgentRuntimeContext(
                transport="api",
                sandbox_mode="per_repo_persistent_sandbox",
                sandbox_name="repo-sandbox",
                sandbox_image="personal-agent-pi-workspace-template",
                duration_seconds=5.1,
            ),
            artifacts=[
                AgentArtifact(kind="workspace", label="Workspace", value=request.workspace_id),
                AgentArtifact(
                    kind="pull_request",
                    label="Pull request",
                    value="https://github.com/example/repo/pull/2",
                    url="https://github.com/example/repo/pull/2",
                ),
            ],
        )

    app.state.container.agent_orchestrator_service = SimpleNamespace(
        approve_repository_push=fake_approve_repository_push
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/pi/repos/push",
            json={"workspace_id": "pi-repo-123"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifacts"][0]["value"] == "pi-repo-123"
    assert payload["artifacts"][1]["url"] == "https://github.com/example/repo/pull/2"


@pytest.mark.asyncio
async def test_computer_use_status_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/automation/computer-use/status")

    assert response.status_code == 200
    assert response.json()["sandbox_name"] == "personal-agent-computer-use"
    assert response.json()["workspace_root"] == "/workspace"
    assert response.json()["actions_enabled"] == []


@pytest.mark.asyncio
async def test_computer_use_provision_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    app.state.container.blaxel_sandbox_service = SimpleNamespace(available=True)

    async def fake_provision():
        return {
            "enabled": True,
            "sandbox_name": "personal-agent-computer-use",
            "status": "DEPLOYED",
            "actions_enabled": [],
        }

    app.state.container.computer_use_service = SimpleNamespace(provision=fake_provision)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/automation/computer-use/provision")

    assert response.status_code == 200
    assert response.json()["sandbox_name"] == "personal-agent-computer-use"
    assert response.json()["status"] == "DEPLOYED"


@pytest.mark.asyncio
async def test_automation_profile_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    app.state.container.job_application_service = SimpleNamespace(
        profile_status=lambda: {
            "profile": {"full_name": "Test User"},
            "missing_fields": ["resume_path"],
            "resume_exists": False,
            "cover_letter_exists": False,
            "computer_use_command_configured": False,
        }
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/automation/profile")

    assert response.status_code == 200
    assert response.json()["profile"]["full_name"] == "Test User"
    assert response.json()["missing_fields"] == ["resume_path"]


@pytest.mark.asyncio
async def test_job_apply_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )

    async def fake_apply_to_job(request):
        assert request.job_url == "https://jobs.example.com/role"
        return SimpleNamespace(
            status="prepared",
            fit_summary="Strong fit for backend/platform work.",
            automation_result={"exit_code": 0},
            profile_missing_fields=[],
        )

    app.state.container.job_application_service = SimpleNamespace(
        apply_to_job=fake_apply_to_job
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/automation/jobs/apply",
            json={
                "job_url": "https://jobs.example.com/role",
                "company_name": "Example",
                "role_title": "Backend Engineer",
                "submit": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "prepared"
    assert response.json()["automation_result"] == {"exit_code": 0}
