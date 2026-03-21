from __future__ import annotations

from personal_agent.hn.models import HNStory, RankedStory
from personal_agent.hn.rollups import TitleRollupBuilder


def build_ranked_story(story_id: int, title: str) -> RankedStory:
    story = HNStory.from_api_payload(
        {
            "id": story_id,
            "type": "story",
            "title": title,
            "score": 50,
            "by": "tester",
            "time": 1_710_000_000 + story_id,
            "descendants": 20,
            "url": "https://example.com/story",
        },
        source_feeds=["top"],
    )
    return RankedStory(
        story=story,
        interesting_score=6.0,
        opportunity_score=0.0,
        summary_score=7.0,
    )


def test_title_rollup_builder_groups_recurring_topics() -> None:
    ranked_stories = [
        build_ranked_story(1, "AI agent for debugging production systems"),
        build_ranked_story(2, "LLM inference stack for low-latency applications"),
        build_ranked_story(3, "New database runtime for API-heavy services"),
        build_ranked_story(4, "SQLite release improves write throughput"),
        build_ranked_story(5, "Show HN: A tiny hardware simulator"),
    ]

    lines = TitleRollupBuilder().build(ranked_stories, limit=4)

    assert any("AI models, agents, and tooling" in line for line in lines)
    assert any("Developer tools, infrastructure, and databases" in line for line in lines)
    assert any("The rest was a mix of one-off essays, launches, and niche curiosities" in line for line in lines)


def test_title_rollup_builder_falls_back_when_no_theme_repeats() -> None:
    ranked_stories = [
        build_ranked_story(1, "The Los Angeles Aqueduct Is Wild"),
        build_ranked_story(2, "A tour of Roman concrete"),
    ]

    lines = TitleRollupBuilder().build(ranked_stories, limit=3)

    assert lines == ["Discussion was fragmented across 2 new stories rather than one dominant topic."]
