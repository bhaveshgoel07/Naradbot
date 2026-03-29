from __future__ import annotations

import asyncio

from personal_agent.hn.models import ChannelDigest, DigestEntry, RankedStory
from personal_agent.hn.rollups import TitleRollupBuilder
from personal_agent.hn.summary_providers import StorySummaryProvider


class StorySummarizer:
    """Digest summarizer that delegates story summaries to a provider."""

    def __init__(
        self,
        provider: StorySummaryProvider,
        *,
        title_rollup_builder: TitleRollupBuilder | None = None,
        summary_topic_count: int = 5,
        concurrency_limit: int = 6,
    ) -> None:
        self.provider = provider
        self.title_rollup_builder = title_rollup_builder or TitleRollupBuilder()
        self.summary_topic_count = summary_topic_count
        self.concurrency_limit = max(1, concurrency_limit)

    async def summarize_channels(
        self,
        ranked_stories: list[RankedStory],
        buckets: dict[str, list[RankedStory]],
        digests: list[ChannelDigest],
    ) -> list[ChannelDigest]:
        digest_by_key = {digest.channel_key: digest for digest in digests}
        self._populate_digest_metadata(ranked_stories, digest_by_key, buckets)

        semaphore = asyncio.Semaphore(self.concurrency_limit)
        tasks: list[asyncio.Task[tuple[str, int, DigestEntry]]] = []

        for channel_key, channel_ranked_stories in buckets.items():
            for index, ranked_story in enumerate(channel_ranked_stories):
                task = asyncio.create_task(
                    self._summarize_entry(
                        semaphore=semaphore,
                        channel_key=channel_key,
                        ranked_story=ranked_story,
                        index=index,
                    )
                )
                tasks.append(task)

        if tasks:
            entry_results = await asyncio.gather(*tasks)
        else:
            entry_results = []

        grouped: dict[str, list[tuple[int, DigestEntry]]] = {}
        for channel_key, index, entry in entry_results:
            grouped.setdefault(channel_key, []).append((index, entry))

        for channel_key, indexed_entries in grouped.items():
            indexed_entries.sort(key=lambda pair: pair[0])
            digest_by_key[channel_key].entries = [entry for _, entry in indexed_entries]

        return list(digest_by_key.values())

    async def _summarize_entry(
        self,
        *,
        semaphore: asyncio.Semaphore,
        channel_key: str,
        ranked_story: RankedStory,
        index: int,
    ) -> tuple[str, int, DigestEntry]:
        async with semaphore:
            summary_result = await self.provider.summarize(ranked_story, channel_key)
        return (
            channel_key,
            index,
            DigestEntry(
                ranked_story=ranked_story,
                summary=summary_result.summary,
                why_it_matters=summary_result.why_it_matters,
            ),
        )

    def _populate_digest_metadata(
        self,
        ranked_stories: list[RankedStory],
        digest_by_key: dict[str, ChannelDigest],
        buckets: dict[str, list[RankedStory]],
    ) -> None:
        summary_digest = digest_by_key.get("summary")
        if summary_digest is not None:
            summary_digest.overview_lines = self.title_rollup_builder.build(
                ranked_stories,
                limit=self.summary_topic_count,
            )
            selected_count = len(buckets.get("interesting", []))
            if selected_count > 0:
                summary_digest.selection_title = (
                    f"Worth reading lives in the read list: "
                    f"{selected_count} unique picks from {len(ranked_stories)} new stories."
                )
