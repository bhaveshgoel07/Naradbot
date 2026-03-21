from __future__ import annotations

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
    ) -> None:
        self.provider = provider
        self.title_rollup_builder = title_rollup_builder or TitleRollupBuilder()
        self.summary_topic_count = summary_topic_count

    async def summarize_channels(
        self,
        ranked_stories: list[RankedStory],
        buckets: dict[str, list[RankedStory]],
        digests: list[ChannelDigest],
    ) -> list[ChannelDigest]:
        digest_by_key = {digest.channel_key: digest for digest in digests}
        self._populate_digest_metadata(ranked_stories, digest_by_key, buckets)
        for channel_key, ranked_stories in buckets.items():
            digest = digest_by_key[channel_key]
            entries: list[DigestEntry] = []
            for ranked_story in ranked_stories:
                summary_result = await self.provider.summarize(ranked_story, channel_key)
                entries.append(
                    DigestEntry(
                        ranked_story=ranked_story,
                        summary=summary_result.summary,
                        why_it_matters=summary_result.why_it_matters,
                    )
                )
            digest.entries = entries
        return list(digest_by_key.values())

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
                    f"Worth reading lives in the read list: {selected_count} unique picks from {len(ranked_stories)} new stories."
                )
