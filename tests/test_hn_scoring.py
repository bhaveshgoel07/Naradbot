from __future__ import annotations

import pytest

from personal_agent.config.settings import Settings
from personal_agent.hn.categorizer import StoryCategorizer
from personal_agent.hn.models import HNStory
from personal_agent.hn.opportunity_embeddings import OpportunityEmbeddingScore
from personal_agent.hn.scorer import StoryScorer
from personal_agent.hn.story_analysis import StoryAnalysisResult


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

    assert ranked[0].opportunity_score > 5
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


def test_categorizer_assigns_unique_stories_and_caps_opportunities_by_top_n() -> None:
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
            url="https://wellfound.com/company/example/jobs/123",
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

    assert opportunity_ids == [4]
    assert summary_ids.isdisjoint(interesting_ids)
    assert summary_ids.isdisjoint(set(opportunity_ids))
    assert interesting_ids.isdisjoint(set(opportunity_ids))
    assert interesting_ids == {2, 3}


def test_story_scorer_metadata_counts_heuristic_signals() -> None:
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

    ranked, metadata = scorer.rank_stories_with_metadata(stories)

    assert len(ranked) == 3
    assert metadata["story_count"] == 3
    assert metadata["interesting_positive_count"] == 3
    assert metadata["opportunity_positive_count"] == 1
    assert metadata["embedding_enabled"] is False


class FakeOpportunityEmbedder:
    enabled = True

    async def rank_stories_against_keywords(
        self,
        stories: list[HNStory],
        hiring_keywords: list[str],
        *,
        non_job_keywords: list[str] | None = None,
        min_similarity: float = 0.45,
        min_margin: float = 0.03,
    ) -> dict[int, OpportunityEmbeddingScore]:
        del hiring_keywords, non_job_keywords, min_similarity, min_margin
        target = next(story for story in stories if story.url and "lever.co" in story.url)
        return {
            target.id: OpportunityEmbeddingScore(
                story_id=target.id,
                similarity=0.81,
                negative_similarity=0.42,
                margin=0.39,
                matched_keyword="A real job post with an open role and a request for candidates to apply.",
                matched_keyword_similarity=0.81,
                matched_negative_keyword="News or commentary about jobs rather than a job application page.",
                channel_tags=["embedding:job-post", "embedding:margin:0.39"],
            )
        }


class FakeStoryAnalysisProvider:
    enabled = True

    async def analyze_many(
        self,
        ranked_stories: list,
        *,
        embedding_matches: dict[int, dict[str, object]],
    ) -> dict[int, StoryAnalysisResult]:
        del embedding_matches
        target = ranked_stories[0].story
        return {
            target.id: StoryAnalysisResult(
                interesting_score=9.2,
                summary_score=9.6,
                opportunity_score=0.0,
                summary="Verified summary from the smaller model.",
                why_it_matters="Verified against the linked article and worth reading now.",
                verification_status="verified",
                verification_notes="Article title and excerpt matched the HN framing.",
            )
        }


@pytest.mark.asyncio
async def test_story_scorer_uses_embedding_matches_to_keep_real_job_posts() -> None:
    scorer = StoryScorer(
        opportunity_embedder=FakeOpportunityEmbedder(),
        job_post_queries=["job post"],
        non_job_keywords=["discussion about hiring"],
    )
    stories = [
        build_story(
            story_id=1,
            title="Senior platform engineer role",
            score=12,
            descendants=2,
            url="https://jobs.lever.co/example",
            text="Remote role with benefits and application link.",
        ),
        build_story(
            story_id=2,
            title="Why hiring is broken",
            score=45,
            descendants=12,
            text="A discussion about jobs, hiring markets, and recruiters.",
        ),
    ]

    ranked, metadata, matches = await scorer.rank_stories_async_with_metadata(stories)
    by_id = {item.story.id: item for item in ranked}

    assert by_id[1].is_opportunity is True
    assert by_id[1].opportunity_verified is True
    assert by_id[1].opportunity_score > 0
    assert by_id[2].is_opportunity is False
    assert by_id[2].opportunity_score == 0.0
    assert metadata["embedding_enabled"] is True
    assert metadata["embedding_match_count"] == 1
    assert matches[1]["margin"] == 0.39


@pytest.mark.asyncio
async def test_story_scorer_applies_all_story_analysis_and_precomputed_summary() -> None:
    scorer = StoryScorer(story_analysis_provider=FakeStoryAnalysisProvider())
    story = build_story(
        story_id=1,
        title="Open source AI infra launch",
        score=80,
        descendants=50,
        url="https://github.com/example/project",
        text="Launch post with benchmarks and implementation notes.",
    )

    ranked, metadata, matches = await scorer.rank_stories_async_with_metadata([story])

    assert matches == {}
    assert metadata["analysis_enabled"] is True
    assert metadata["analysis_story_count"] == 1
    assert ranked[0].generated_summary == "Verified summary from the smaller model."
    assert ranked[0].generated_why_it_matters.startswith("Verified against")
    assert ranked[0].verification_status == "verified"
    assert ranked[0].analysis_source == "llm_story_analysis"
    assert ranked[0].summary_score > 8
