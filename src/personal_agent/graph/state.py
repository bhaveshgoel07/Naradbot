from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from personal_agent.hn.models import ChannelDigest, FeedName, HNStory, RankedStory


@dataclass(slots=True)
class HNWorkflowRequest:
    """Input contract for the Hacker News workflow graph."""

    trigger_source: str
    requested_by: str | None = None
    publish_to_discord: bool = True


@dataclass(slots=True)
class HNWorkflowState:
    """Mutable state passed through the Hacker News orchestration graph."""

    request: HNWorkflowRequest
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    story_sources: dict[int, list[FeedName]] = field(default_factory=dict)
    unprocessed_story_sources: dict[int, list[FeedName]] = field(default_factory=dict)
    stories: list[HNStory] = field(default_factory=list)
    prepared_ranked_stories: list[RankedStory] = field(default_factory=list)
    prepared_score_metadata: dict[str, Any] = field(default_factory=dict)
    editorial_ranked_stories: list[RankedStory] = field(default_factory=list)
    editorial_score_metadata: dict[str, Any] = field(default_factory=dict)
    opportunity_ranked_stories: list[RankedStory] = field(default_factory=list)
    opportunity_score_metadata: dict[str, Any] = field(default_factory=dict)
    ranked_stories: list[RankedStory] = field(default_factory=list)
    channel_buckets: dict[str, list[RankedStory]] = field(default_factory=dict)
    digests: list[ChannelDigest] = field(default_factory=list)
    published_messages: dict[str, str] = field(default_factory=dict)
    opportunity_embedding_matches: dict[int, dict[str, Any]] = field(
        default_factory=dict
    )
    score_metadata: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def finalize(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
