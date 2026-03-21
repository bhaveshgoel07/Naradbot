from __future__ import annotations

import asyncio

from personal_agent.config.settings import Settings
from personal_agent.hn.client import HackerNewsClient
from personal_agent.hn.models import FeedName, HNStory


class HNFetcher:
    """Fetch story IDs and story payloads from the Hacker News API."""

    def __init__(self, client: HackerNewsClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def fetch_candidate_story_ids(self) -> dict[int, list[FeedName]]:
        feeds: list[FeedName] = ["top", "new"]
        if self.settings.hn_include_best:
            feeds.append("best")

        feed_results = await asyncio.gather(*(self.client.fetch_story_ids(feed) for feed in feeds))
        id_to_feeds: dict[int, set[FeedName]] = {}
        for feed, story_ids in zip(feeds, feed_results, strict=True):
            for story_id in story_ids[: self.settings.hn_fetch_limit]:
                id_to_feeds.setdefault(story_id, set()).add(feed)

        return {
            story_id: sorted(source_feeds)
            for story_id, source_feeds in id_to_feeds.items()
        }

    async def fetch_stories(self, story_sources: dict[int, list[FeedName]]) -> list[HNStory]:
        coroutines = [
            self.client.fetch_story(story_id=story_id, source_feeds=source_feeds)
            for story_id, source_feeds in story_sources.items()
        ]
        stories = await asyncio.gather(*coroutines)
        cleaned = [story for story in stories if story is not None]
        return sorted(cleaned, key=lambda story: (story.score, story.descendants), reverse=True)
