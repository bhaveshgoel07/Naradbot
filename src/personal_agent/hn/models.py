from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

FeedName = Literal["top", "new", "best"]
ChannelKey = Literal["summary", "interesting", "opportunities"]


@dataclass(slots=True)
class HNStory:
    """Normalized Hacker News story payload used throughout the pipeline."""

    id: int
    title: str
    url: str | None
    score: int
    by: str
    created_at: datetime
    descendants: int
    text: str | None
    source_feeds: list[FeedName] = field(default_factory=list)

    @property
    def domain(self) -> str:
        if not self.url:
            return "news.ycombinator.com"
        return urlparse(self.url).netloc or "news.ycombinator.com"

    @property
    def permalink(self) -> str:
        return f"https://news.ycombinator.com/item?id={self.id}"

    @classmethod
    def from_api_payload(cls, payload: dict, source_feeds: list[FeedName]) -> "HNStory | None":
        if payload.get("type") != "story":
            return None
        if payload.get("deleted") or payload.get("dead"):
            return None
        if not payload.get("title"):
            return None

        return cls(
            id=int(payload["id"]),
            title=str(payload["title"]),
            url=payload.get("url"),
            score=int(payload.get("score", 0) or 0),
            by=str(payload.get("by", "unknown")),
            created_at=datetime.fromtimestamp(int(payload.get("time", 0) or 0), tz=timezone.utc),
            descendants=int(payload.get("descendants", 0) or 0),
            text=payload.get("text"),
            source_feeds=source_feeds,
        )


@dataclass(slots=True)
class RankedStory:
    """Story plus ranking metadata for category selection."""

    story: HNStory
    interesting_score: float
    opportunity_score: float
    summary_score: float
    reason_tags: list[str] = field(default_factory=list)
    interesting_reason_tags: list[str] = field(default_factory=list)
    opportunity_reason_tags: list[str] = field(default_factory=list)
    summary_reason_tags: list[str] = field(default_factory=list)

    @property
    def is_opportunity(self) -> bool:
        return bool(self.opportunity_reason_tags)


@dataclass(slots=True)
class DigestEntry:
    """Short human-readable summary prepared for Discord delivery."""

    ranked_story: RankedStory
    summary: str
    why_it_matters: str


@dataclass(slots=True)
class ChannelDigest:
    """Collection of entries destined for a single Discord channel."""

    channel_key: ChannelKey
    title: str
    overview_lines: list[str] = field(default_factory=list)
    selection_title: str | None = None
    entries: list[DigestEntry] = field(default_factory=list)
