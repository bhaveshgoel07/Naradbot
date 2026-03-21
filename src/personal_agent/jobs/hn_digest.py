from __future__ import annotations

import asyncio
import logging

from personal_agent.config.settings import get_settings
from personal_agent.container import build_container
from personal_agent.logging import configure_logging

logger = logging.getLogger(__name__)


async def _run_hn_digest_once() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)

    if not settings.discord_webhooks_enabled:
        logger.warning(
            "Scheduled one-shot HN job is running without Discord webhooks configured; digest delivery will be skipped."
        )
        container.workflow_nodes.discord_sender = None

    result = await container.hn_service.run(
        trigger_source="job",
        requested_by="hn_digest_once",
        publish_to_discord=settings.discord_webhooks_enabled,
    )
    logger.info("Completed one-shot Hacker News digest run: %s", result)


def main() -> None:
    asyncio.run(_run_hn_digest_once())


if __name__ == "__main__":
    main()
