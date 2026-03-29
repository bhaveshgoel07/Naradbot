from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from personal_agent.agent.models import (
    AgentMessageRequest,
    AgentRepositoryPushRequest,
    AgentRepositoryRequest,
)
from personal_agent.automation.models import (
    JobApplicationRequest,
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
    session_id: str | None = None


class BlaxelInferenceRequestBody(BaseModel):
    inputs: Any
    workdir: str | None = None
    files: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None
    session_id: str | None = None


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
    allow_push: bool = False


class PiRepoPushRequestBody(BaseModel):
    workspace_id: str
    pr_title: str | None = None
    pr_body: str | None = None
    base_branch: str | None = None


class JobApplyRequestBody(BaseModel):
    job_url: str
    company_name: str | None = None
    role_title: str | None = None
    notes: str | None = None
    submit: bool = False


def _coerce_blaxel_inputs(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [
            item if isinstance(item, str) else json.dumps(item, sort_keys=True)
            for item in value
        ]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("prompt", "input", "message", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return str(value).strip()


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

    @app.post("/")
    async def run_blaxel_inference(body: BlaxelInferenceRequestBody) -> PlainTextResponse:
        prompt = _coerce_blaxel_inputs(body.inputs)
        if not prompt:
            raise HTTPException(status_code=400, detail="Request body must include non-empty inputs.")

        result = await container.agent_orchestrator_service.handle_message(
            AgentMessageRequest(
                prompt=prompt,
                transport="api",
                workdir=body.workdir,
                files=list(body.files),
                tools=list(body.tools),
                provider=body.provider,
                model=body.model,
                thinking=body.thinking,
                append_system_prompt=body.append_system_prompt,
                timeout_seconds=body.timeout_seconds,
                session_id=body.session_id,
            )
        )
        if not result.ok:
            raise HTTPException(
                status_code=500,
                detail=result.error_text or "Agent task failed.",
            )
        return PlainTextResponse(result.final_text)

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
        return container.agent_orchestrator_service.status()

    @app.post("/agents/pi/run")
    async def run_pi_task(body: PiRunRequestBody) -> dict[str, object]:
        result = await container.agent_orchestrator_service.handle_message(
            AgentMessageRequest(
                prompt=body.prompt,
                transport="api",
                workdir=body.workdir,
                files=list(body.files),
                tools=list(body.tools),
                provider=body.provider,
                model=body.model,
                thinking=body.thinking,
                append_system_prompt=body.append_system_prompt,
                timeout_seconds=body.timeout_seconds,
                session_id=body.session_id,
            )
        )
        return asdict(result)

    @app.post("/agents/pi/repos/run")
    async def run_pi_repo_task(body: PiRepoRunRequestBody) -> dict[str, object]:
        result = await container.agent_orchestrator_service.prepare_repository(
            AgentRepositoryRequest(
                repo_url=body.repo_url,
                prompt=body.prompt,
                transport="api",
                requested_by="fastapi",
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
                allow_push=body.allow_push,
            )
        )
        return asdict(result)

    @app.post("/agents/pi/repos/push")
    async def push_pi_repo_task(body: PiRepoPushRequestBody) -> dict[str, object]:
        result = await container.agent_orchestrator_service.approve_repository_push(
            AgentRepositoryPushRequest(
                workspace_id=body.workspace_id,
                transport="api",
                requested_by="fastapi",
                pr_title=body.pr_title,
                pr_body=body.pr_body,
                base_branch=body.base_branch,
            )
        )
        return asdict(result)

    @app.get("/automation/profile")
    async def automation_profile() -> dict[str, object]:
        return container.job_application_service.profile_status()

    @app.get("/automation/computer-use/status")
    async def computer_use_status() -> dict[str, object]:
        return container.computer_use_service.status()

    @app.post("/automation/computer-use/provision")
    async def provision_computer_use_sandbox() -> dict[str, object]:
        if not container.blaxel_sandbox_service.available:
            raise HTTPException(
                status_code=503,
                detail="Computer-use sandboxing is disabled.",
            )
        return await container.computer_use_service.provision()

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
