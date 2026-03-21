from __future__ import annotations

from personal_agent.config.settings import Settings
from personal_agent.hn.categorizer import StoryCategorizer
from personal_agent.hn.models import HNStory
from personal_agent.hn.scorer import StoryScorer


def build_story(
    *,
    story_id: int,
    title: str,
    score: int,
    descendants: int,
    url: str | None = None,
    text: str | None = None,
    created_at: int | None = None,
) -> HNStory:
    return HNStory.from_api_payload(
        {
            "id": story_id,
            "type": "story",
            "title": title,
            "score": score,
            "by": "tester",
            "time": created_at or 1_710_000_000,
            "descendants": descendants,
            "url": url,
            "text": text,
        },
        source_feeds=["top"],
    )


def test_story_scorer_prioritizes_opportunity_posts() -> None:
    scorer = StoryScorer()
    story = build_story(
        story_id=1,
        title="Who is hiring? Remote founding engineer roles",
        score=120,
        descendants=220,
        url="https://jobs.lever.co/example",
    )

    ranked = scorer.rank_stories([story])

    assert ranked[0].opportunity_score > 8
    assert ranked[0].summary_score > ranked[0].interesting_score


def test_categorizer_builds_expected_channel_buckets() -> None:
    settings = Settings(
        summary_top_n=2,
        interesting_top_n=1,
        opportunities_top_n=1,
    )
    scorer = StoryScorer()
    stories = [
        build_story(
            story_id=1,
            title="Open source AI infra launch",
            score=80,
            descendants=50,
            url="https://github.com/example/project",
        ),
        build_story(
            story_id=2,
            title="Who is hiring? Remote contract roles",
            score=140,
            descendants=150,
            url="https://jobs.lever.co/example",
        ),
        build_story(
            story_id=3,
            title="The Los Angeles Aqueduct Is Wild",
            score=165,
            descendants=93,
            url="https://practical.engineering/blog/2026/3/17/the-los-angeles-aqueduct-is-wild",
        ),
    ]

    ranked = scorer.rank_stories(stories)
    categorizer = StoryCategorizer(settings)
    buckets = categorizer.build_channel_buckets(ranked)

    assert len(buckets["summary"]) == 1
    assert len(buckets["interesting"]) == 1
    assert len(buckets["opportunities"]) == 1
    assert buckets["opportunities"][0].story.id == 2
    assert buckets["summary"][0].story.id == 3
    assert buckets["interesting"][0].story.id == 1


def test_opportunity_scoring_requires_explicit_opportunity_signals() -> None:
    scorer = StoryScorer()
    story = build_story(
        story_id=1,
        title="Launch HN: Sitefire (YC W26) - Automating actions to improve AI visibility",
        score=18,
        descendants=16,
        text="Our platform helps brands improve visibility in AI search.",
    )

    ranked = scorer.rank_stories([story])

    assert ranked[0].opportunity_score == 0.0
    assert ranked[0].opportunity_reason_tags == []


def test_categorizer_assigns_unique_stories_and_keeps_all_opportunities() -> None:
    settings = Settings(
        summary_top_n=3,
        interesting_top_n=2,
        opportunities_top_n=1,
    )
    scorer = StoryScorer()
    stories = [
        build_story(
            story_id=1,
            title="Who is hiring? Remote contract roles",
            score=140,
            descendants=150,
            url="https://jobs.lever.co/example",
            created_at=1_710_000_100,
        ),
        build_story(
            story_id=2,
            title="Open source AI infra launch",
            score=120,
            descendants=60,
            url="https://github.com/example/project",
            created_at=1_710_000_200,
        ),
        build_story(
            story_id=3,
            title="AI agent for debugging production systems",
            score=110,
            descendants=55,
            url="https://openai.com/example",
            created_at=1_710_000_300,
        ),
        build_story(
            story_id=4,
            title="Freelance ML engineer needed",
            score=3,
            descendants=0,
            text="Remote freelance opportunity for an ML engineer.",
            created_at=1_710_000_400,
        ),
    ]

    ranked = scorer.rank_stories(stories)
    categorizer = StoryCategorizer(settings)
    buckets = categorizer.build_channel_buckets(ranked)

    summary_ids = {story.story.id for story in buckets["summary"]}
    interesting_ids = {story.story.id for story in buckets["interesting"]}
    opportunity_ids = [story.story.id for story in buckets["opportunities"]]

    assert opportunity_ids == [4, 1]
    assert summary_ids.isdisjoint(interesting_ids)
    assert summary_ids.isdisjoint(set(opportunity_ids))
    assert interesting_ids.isdisjoint(set(opportunity_ids))
    assert interesting_ids == {2, 3}
