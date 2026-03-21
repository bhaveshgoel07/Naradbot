from __future__ import annotations

from collections.abc import Callable

from personal_agent.config.settings import Settings
from personal_agent.hn.models import ChannelDigest, DigestEntry, RankedStory


class StoryCategorizer:
    """Selects the best stories for each Discord digest channel."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_channel_buckets(self, ranked_stories: list[RankedStory]) -> dict[str, list[RankedStory]]:
        used_story_ids: set[int] = set()

        opportunities = self._all_opportunities(ranked_stories)
        used_story_ids.update(story.story.id for story in opportunities)

        interesting = self._take_unique(
            sorted(ranked_stories, key=lambda item: item.interesting_score, reverse=True),
            limit=self.settings.interesting_top_n,
            used_story_ids=used_story_ids,
            predicate=lambda story: story.interesting_score >= 3.0,
        )
        used_story_ids.update(story.story.id for story in interesting)

        summary = self._take_unique(
            sorted(ranked_stories, key=lambda item: item.summary_score, reverse=True),
            limit=self.settings.summary_top_n,
            used_story_ids=used_story_ids,
        )

        return {
            "summary": summary,
            "interesting": interesting,
            "opportunities": opportunities,
        }

    @staticmethod
    def _all_opportunities(ranked_stories: list[RankedStory]) -> list[RankedStory]:
        return [
            story
            for story in sorted(ranked_stories, key=lambda item: item.story.created_at, reverse=True)
            if story.is_opportunity
        ]

    @staticmethod
    def _take_unique(
        ranked_stories: list[RankedStory],
        *,
        limit: int,
        used_story_ids: set[int],
        predicate: Callable[[RankedStory], bool] | None = None,
    ) -> list[RankedStory]:
        selected: list[RankedStory] = []
        for story in ranked_stories:
            if story.story.id in used_story_ids:
                continue
            if predicate is not None and not predicate(story):
                continue
            selected.append(story)
            if len(selected) >= limit:
                break
        return selected

    def build_empty_digests(self) -> list[ChannelDigest]:
        return [
            ChannelDigest(channel_key="summary", title="Hacker News Rollup"),
            ChannelDigest(channel_key="interesting", title="Worth Reading from Hacker News"),
            ChannelDigest(channel_key="opportunities", title="Hacker News Opportunities"),
        ]

    @staticmethod
    def assign_story_channels(digests: list[ChannelDigest]) -> dict[int, list[str]]:
        membership: dict[int, list[str]] = {}
        for digest in digests:
            for entry in digest.entries:
                membership.setdefault(entry.ranked_story.story.id, []).append(digest.channel_key)
        return membership
