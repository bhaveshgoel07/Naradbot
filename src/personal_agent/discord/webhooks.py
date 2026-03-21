from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from personal_agent.discord.messages import split_discord_message_content

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscordWebhookSender:
    """Posts digest messages directly to Discord webhooks."""

    webhook_urls: dict[str, str | None]

    async def send_digest_message(self, channel_key: str, message: str) -> None:
        webhook_url = self.webhook_urls.get(channel_key)
        if not webhook_url:
            logger.warning("No Discord webhook configured for %s", channel_key)
            return

        message_parts = split_discord_message_content(message)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for index, part in enumerate(message_parts, start=1):
                logger.info(
                    "Sending Discord webhook chunk %s/%s for %s length=%s",
                    index,
                    len(message_parts),
                    channel_key,
                    len(part),
                )
                async with session.post(webhook_url, json={"content": part}) as response:
                    if response.status >= 400:
                        body = await response.text()
                        raise RuntimeError(
                            f"Discord webhook publish failed for {channel_key} with status {response.status}: {body[:200]}"
                        )
