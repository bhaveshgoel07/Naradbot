from __future__ import annotations

import aiohttp

from personal_agent.hn.models import FeedName, HNStory


class HackerNewsClient:
    """Thin async client for the public Hacker News Firebase API."""

    BASE_URL = "https://hacker-news.firebaseio.com/v0"

    async def fetch_story_ids(self, feed: FeedName) -> list[int]:
        url = f"{self.BASE_URL}/{feed}stories.json"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.json()
        return [int(story_id) for story_id in payload]

    async def fetch_story(self, story_id: int, source_feeds: list[FeedName]) -> HNStory | None:
        url = f"{self.BASE_URL}/item/{story_id}.json"
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.json()
        if not payload:
            return None
        return HNStory.from_api_payload(payload, source_feeds)
