from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from personal_agent.config.settings import Settings, get_settings
from personal_agent.container import ServiceContainer, build_container
from personal_agent.logging import configure_logging

logger = logging.getLogger(__name__)


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
