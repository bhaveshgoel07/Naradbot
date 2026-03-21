from __future__ import annotations

from dataclasses import dataclass

from personal_agent.graph.state import HNWorkflowState
from personal_agent.hn.categorizer import StoryCategorizer
from personal_agent.hn.fetcher import HNFetcher
from personal_agent.hn.publisher import DigestPublisher, DiscordSender
from personal_agent.hn.summarizer import StorySummarizer
from personal_agent.hn.scorer import StoryScorer
from personal_agent.storage.repositories import HNRunRepository, ProcessedStoryRepository


@dataclass(slots=True)
class HNWorkflowNodes:
    """Readable orchestration steps for the Hacker News graph."""

    fetcher: HNFetcher
    processed_story_repository: ProcessedStoryRepository
    run_repository: HNRunRepository
    scorer: StoryScorer
    categorizer: StoryCategorizer
    summarizer: StorySummarizer
    publisher: DigestPublisher
    discord_sender: DiscordSender | None = None

    async def fetch_story_sources(self, state: HNWorkflowState) -> HNWorkflowState:
        state.story_sources = await self.fetcher.fetch_candidate_story_ids()
        state.details["candidate_story_count"] = len(state.story_sources)
        return state

    async def deduplicate_story_sources(self, state: HNWorkflowState) -> HNWorkflowState:
        unprocessed_ids = self.processed_story_repository.filter_unprocessed_ids(list(state.story_sources))
        state.unprocessed_story_sources = {
            story_id: state.story_sources[story_id]
            for story_id in unprocessed_ids
        }
        state.details["unprocessed_story_count"] = len(state.unprocessed_story_sources)
        return state

    async def fetch_story_details(self, state: HNWorkflowState) -> HNWorkflowState:
        state.stories = await self.fetcher.fetch_stories(state.unprocessed_story_sources)
        state.details["fetched_story_count"] = len(state.stories)
        return state

    async def score_stories(self, state: HNWorkflowState) -> HNWorkflowState:
        state.ranked_stories = self.scorer.rank_stories(state.stories)
        return state

    async def categorize_stories(self, state: HNWorkflowState) -> HNWorkflowState:
        state.channel_buckets = self.categorizer.build_channel_buckets(state.ranked_stories)
        state.details["bucket_sizes"] = {
            channel_key: len(stories)
            for channel_key, stories in state.channel_buckets.items()
        }
        return state

    async def summarize_digests(self, state: HNWorkflowState) -> HNWorkflowState:
        empty_digests = self.categorizer.build_empty_digests()
        state.digests = await self.summarizer.summarize_channels(
            state.ranked_stories,
            state.channel_buckets,
            empty_digests,
        )
        return state

    async def publish_digests(self, state: HNWorkflowState) -> HNWorkflowState:
        sender = self.discord_sender if state.request.publish_to_discord else None
        state.published_messages = await self.publisher.publish(state.digests, sender)
        return state

    async def persist_results(self, state: HNWorkflowState) -> HNWorkflowState:
        membership = self.categorizer.assign_story_channels(state.digests)
        reviewed_membership = {
            story.id: membership.get(story.id, ["seen"])
            for story in state.stories
        }
        self.processed_story_repository.mark_processed(reviewed_membership)
        state.finalize()
        self.run_repository.record_run(
            trigger_source=state.request.trigger_source,
            requested_by=state.request.requested_by,
            status="completed" if not state.errors else "completed_with_errors",
            story_count=len(state.stories),
            started_at=state.started_at,
            finished_at=state.finished_at or state.started_at,
            details={
                **state.details,
                "processed_story_ids": sorted(reviewed_membership),
            },
        )
        return state
