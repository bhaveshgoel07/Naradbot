from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from personal_agent.hn.formatters import DiscordDigestFormatter
from personal_agent.hn.models import ChannelDigest

logger = logging.getLogger(__name__)

DiscordSender = Callable[[str, str], Awaitable[None]]


class DigestPublisher:
    """Publishes channel digests through an abstract Discord sender."""

    def __init__(self, formatter: DiscordDigestFormatter) -> None:
        self.formatter = formatter

    async def publish(self, digests: list[ChannelDigest], sender: DiscordSender | None) -> dict[str, str]:
        rendered_messages: dict[str, str] = {}
        for digest in digests:
            message = self.formatter.format_digest(digest)
            rendered_messages[digest.channel_key] = message
            if sender is None:
                logger.info("Rendered %s digest without Discord sender", digest.channel_key)
                continue
            await sender(digest.channel_key, message)
        return rendered_messages
