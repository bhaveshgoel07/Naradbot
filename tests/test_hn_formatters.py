from __future__ import annotations

from personal_agent.hn.formatters import DiscordDigestFormatter
from personal_agent.hn.models import ChannelDigest, DigestEntry, HNStory, RankedStory


def test_opportunities_digest_omits_hn_point_stats() -> None:
    story = HNStory.from_api_payload(
        {
            "id": 123,
            "type": "story",
            "title": "Who is hiring? Remote founding engineer",
            "score": 88,
            "by": "tester",
            "time": 1_710_000_000,
            "descendants": 42,
            "url": "https://jobs.lever.co/example",
        },
        source_feeds=["top"],
    )
    ranked_story = RankedStory(
        story=story,
        interesting_score=2.0,
        opportunity_score=10.0,
        summary_score=5.0,
        opportunity_reason_tags=["keyword:hiring", "keyword:remote"],
    )
    digest = ChannelDigest(
        channel_key="opportunities",
        title="Hacker News Opportunities",
        entries=[
            DigestEntry(
                ranked_story=ranked_story,
                summary="Hiring for a remote founding engineer role.",
                why_it_matters="Matched opportunity signals: keyword:hiring, keyword:remote.",
            )
        ],
    )

    rendered = DiscordDigestFormatter().format_digest(digest)

    assert "Score:" not in rendered
    assert "Comments:" not in rendered
    assert "Posted: 2024-03-09" in rendered


def test_summary_digest_renders_rollup_without_falling_back_to_empty_message() -> None:
    digest = ChannelDigest(
        channel_key="summary",
        title="Hacker News Rollup",
        overview_lines=[
            "AI models, agents, and tooling kept coming up across 4 titles.",
            "Developer tools, infrastructure, and databases kept coming up across 3 titles.",
        ],
        selection_title="Worth reading lives in the read list: 12 unique picks from 27 new stories.",
    )

    rendered = DiscordDigestFormatter().format_digest(digest)

    assert "What Hacker News talked about:" in rendered
    assert "AI models, agents, and tooling kept coming up across 4 titles." in rendered
    assert "Worth reading lives in the read list: 12 unique picks from 27 new stories." in rendered
    assert "No new stories matched this digest" not in rendered
