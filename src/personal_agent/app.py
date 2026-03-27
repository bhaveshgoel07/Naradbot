from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from personal_agent.automation.models import (
    JobApplicationRequest,
    PiRepositoryTaskRequest,
    PiTaskRequest,
)
from personal_agent.config.settings import Settings, get_settings
from personal_agent.container import ServiceContainer, build_container
from personal_agent.logging import configure_logging

logger = logging.getLogger(__name__)


class PiRunRequestBody(BaseModel):
    prompt: str
    workdir: str | None = None
    files: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None


class PiRepoRunRequestBody(BaseModel):
    repo_url: str
    prompt: str
    base_branch: str | None = None
    branch_name: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None
    tools: list[str] = Field(default_factory=list)


class JobApplyRequestBody(BaseModel):
    job_url: str
    company_name: str | None = None
    role_title: str | None = None
    notes: str | None = None
    submit: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    container = build_container(runtime_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("Starting personal agent application")
        should_run_background_services = runtime_settings.environment != "test"

        if should_run_background_services:
            container.scheduler_service.start()

        bot_task: asyncio.Task[None] | None = None
        if should_run_background_services and container.discord_bot is not None:
            bot_task = asyncio.create_task(container.discord_bot.start(runtime_settings.discord_bot_token.get_secret_value()))

        if runtime_settings.bot_startup_hn_run:
            await container.hn_service.run(
                trigger_source="startup",
                requested_by=None,
                publish_to_discord=container.workflow_nodes.discord_sender is not None,
            )

        try:
            yield
        finally:
            if should_run_background_services:
                container.scheduler_service.shutdown()
            if bot_task is not None and container.discord_bot is not None:
                await container.discord_bot.close()
            if bot_task is not None:
                await bot_task
            logger.info("Stopped personal agent application")

    app = FastAPI(title=runtime_settings.app_name, lifespan=lifespan)
    app.state.container = container

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": runtime_settings.environment}

    @app.get("/status")
    async def status() -> dict[str, object]:
        recent_runs = container.run_repository.recent_runs(limit=5)
        return {
            "environment": runtime_settings.environment,
            "discord_enabled": runtime_settings.discord_enabled,
            "discord_publish_enabled": runtime_settings.discord_publish_enabled,
            "processed_story_count": container.processed_story_repository.processed_count(),
            "recent_runs": recent_runs,
        }

    @app.post("/workflows/hacker-news/run")
    async def run_hacker_news() -> dict[str, object]:
        return await container.hn_service.run(
            trigger_source="api",
            requested_by="fastapi",
            publish_to_discord=container.workflow_nodes.discord_sender is not None,
        )

    @app.get("/agents/pi/status")
    async def pi_status() -> dict[str, object]:
        return container.pi_coding_agent_service.status()

    @app.post("/agents/pi/run")
    async def run_pi_task(body: PiRunRequestBody) -> dict[str, object]:
        result = await container.pi_coding_agent_service.run_task(
            PiTaskRequest(
                prompt=body.prompt,
                workdir=body.workdir,
                files=list(body.files),
                tools=list(body.tools),
                provider=body.provider,
                model=body.model,
                thinking=body.thinking,
                append_system_prompt=body.append_system_prompt,
                timeout_seconds=body.timeout_seconds,
            )
        )
        return {
            "available": result.available,
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": result.duration_seconds,
        }

    @app.post("/agents/pi/repos/run")
    async def run_pi_repo_task(body: PiRepoRunRequestBody) -> dict[str, object]:
        result = await container.pi_coding_agent_service.run_repository_task(
            PiRepositoryTaskRequest(
                repo_url=body.repo_url,
                prompt=body.prompt,
                base_branch=body.base_branch,
                branch_name=body.branch_name,
                pr_title=body.pr_title,
                pr_body=body.pr_body,
                provider=body.provider,
                model=body.model,
                thinking=body.thinking,
                append_system_prompt=body.append_system_prompt,
                timeout_seconds=body.timeout_seconds,
                tools=list(body.tools),
                requested_by="fastapi",
            )
        )
        return {
            "available": result.available,
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": result.duration_seconds,
            "sandbox_mode": result.sandbox_mode,
            "workspace_dir": result.workspace_dir,
            "repo_dir": result.repo_dir,
            "repo_url": result.repo_url,
            "base_branch": result.base_branch,
            "branch_name": result.branch_name,
            "commit_sha": result.commit_sha,
            "pr_url": result.pr_url,
            "changes_detected": result.changes_detected,
            "review_required": result.review_required,
            "setup_commands": result.setup_commands,
            "git_status": result.git_status,
        }

    @app.get("/automation/profile")
    async def automation_profile() -> dict[str, object]:
        return container.job_application_service.profile_status()

    @app.post("/automation/jobs/apply")
    async def apply_to_job(body: JobApplyRequestBody) -> dict[str, object]:
        result = await container.job_application_service.apply_to_job(
            JobApplicationRequest(
                job_url=body.job_url,
                company_name=body.company_name,
                role_title=body.role_title,
                notes=body.notes,
                submit=body.submit,
            )
        )
        return {
            "status": result.status,
            "fit_summary": result.fit_summary,
            "automation_result": result.automation_result,
            "profile_missing_fields": result.profile_missing_fields,
        }

    @app.get("/workflows/hacker-news/graph")
    async def hacker_news_graph(format: str = Query(default="mermaid", pattern="^(ascii|mermaid|png)$")) -> Response:
        workflow = container.hn_workflow

        if format == "ascii":
            try:
                return PlainTextResponse(workflow.draw_ascii())
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Unable to render ASCII graph: {exc}",
                ) from exc
        if format == "mermaid":
            return PlainTextResponse(workflow.draw_mermaid(), media_type="text/vnd.mermaid")

        try:
            png_bytes = workflow.draw_png()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Unable to render PNG graph: {exc}",
            ) from exc
        return Response(content=png_bytes, media_type="image/png")

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "personal_agent.app:create_app",
        host=settings.api_host,
        port=settings.api_port,
        factory=True,
        reload=settings.environment == "development",
    )
