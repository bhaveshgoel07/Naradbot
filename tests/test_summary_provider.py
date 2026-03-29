from __future__ import annotations

import pytest

from personal_agent.config.settings import Settings
from personal_agent.container import build_summary_provider
from personal_agent.hn.models import HNStory, RankedStory
from personal_agent.hn.summary_providers import HeuristicStorySummaryProvider, NebiusStorySummaryProvider


def build_ranked_story() -> RankedStory:
    story = HNStory.from_api_payload(
        {
            "id": 123,
            "type": "story",
            "title": "Open source AI infra launch",
            "score": 88,
            "by": "tester",
            "time": 1_710_000_000,
            "descendants": 42,
            "url": "https://github.com/example/project",
            "text": "Launch post for an open source AI infrastructure tool.",
        },
        source_feeds=["top"],
    )
    return RankedStory(
        story=story,
        interesting_score=7.4,
        opportunity_score=0.0,
        summary_score=8.1,
        reason_tags=["keyword:open source", "keyword:ai", "domain:github.com"],
    )


def test_build_summary_provider_returns_nebius_provider() -> None:
    settings = Settings(
        llm_provider="nebius",
        llm_model="NousResearch/Hermes-4-70B",
        llm_base_url="https://api.tokenfactory.nebius.com/v1/",
        llm_api_key="test-key",
    )

    provider = build_summary_provider(settings)

    assert isinstance(provider, NebiusStorySummaryProvider)
    assert provider.model == "NousResearch/Hermes-4-70B"


@pytest.mark.asyncio
async def test_nebius_provider_falls_back_without_api_key() -> None:
    provider = NebiusStorySummaryProvider(
        model="NousResearch/Hermes-4-70B",
        base_url="https://api.tokenfactory.nebius.com/v1/",
        api_key=None,
        fallback_provider=HeuristicStorySummaryProvider(),
    )

    result = await provider.summarize(build_ranked_story(), "interesting")

    assert "trending on Hacker News" in result.summary or "Launch post" in result.summary
    assert "worth-reading signals" in result.why_it_matters


@pytest.mark.asyncio
async def test_heuristic_provider_prefers_precomputed_story_analysis() -> None:
    ranked_story = build_ranked_story()
    ranked_story.generated_summary = "Precomputed summary"
    ranked_story.generated_why_it_matters = "Precomputed rationale"

    result = await HeuristicStorySummaryProvider().summarize(ranked_story, "summary")

    assert result.summary == "Precomputed summary"
    assert result.why_it_matters == "Precomputed rationale"
