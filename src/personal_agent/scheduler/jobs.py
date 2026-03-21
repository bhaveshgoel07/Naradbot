from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from personal_agent.config.settings import Settings
from personal_agent.hn.service import HNService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Owns recurring application jobs."""

    def __init__(self, settings: Settings, hn_service: HNService) -> None:
        self.settings = settings
        self.hn_service = hn_service
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self.scheduler.add_job(
            self._run_hn_digest,
            trigger="interval",
            hours=self.settings.hn_poll_hours,
            id="hn-digest",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started with Hacker News interval=%s hours", self.settings.hn_poll_hours)

    async def _run_hn_digest(self) -> None:
        logger.info("Running scheduled Hacker News digest")
        await self.hn_service.run(
            trigger_source="scheduler",
            requested_by=None,
            publish_to_discord=True,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
