from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from personal_agent.app import create_app
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
    app.state.container.pi_coding_agent_service = SimpleNamespace(
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

    async def fake_run_repository_task(request):
        assert request.repo_url == "https://github.com/example/repo"
        assert request.prompt == "Fix the failing test and open a PR."
        return SimpleNamespace(
            available=True,
            command=["pi", "-p", "Fix the failing test and open a PR."],
            exit_code=0,
            stdout="Applied patch.",
            stderr="",
            duration_seconds=12.5,
            sandbox_mode="isolated_repo_clone",
            workspace_dir="/tmp/personal-agent/pi/pi-repo-123",
            repo_dir="/tmp/personal-agent/pi/pi-repo-123/repo",
            repo_url=request.repo_url,
            base_branch="main",
            branch_name="personal-agent/test-branch",
            commit_sha="abc123",
            pr_url="https://github.com/example/repo/pull/1",
            changes_detected=True,
            review_required=True,
            setup_commands=[["git", "clone"]],
            git_status="M src/app.py",
        )

    app.state.container.pi_coding_agent_service = SimpleNamespace(
        run_repository_task=fake_run_repository_task
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
    assert response.json()["sandbox_mode"] == "isolated_repo_clone"
    assert response.json()["pr_url"] == "https://github.com/example/repo/pull/1"
    assert response.json()["review_required"] is True


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
