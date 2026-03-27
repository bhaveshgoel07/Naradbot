from __future__ import annotations

from datetime import datetime, timezone

import pytest

from personal_agent.hn.models import HNStory
from personal_agent.hn.opportunity_embeddings import (
    NebiusOpportunityEmbedder,
    cosine_similarity,
)


def build_story(
    *,
    story_id: int,
    title: str = "Who is hiring? Backend engineer role",
    url: str | None = "https://jobs.example.com/role",
    text: str | None = "Remote role for backend engineers.",
) -> HNStory:
    return HNStory(
        id=story_id,
        title=title,
        url=url,
        score=100,
        by="tester",
        created_at=datetime.now(timezone.utc),
        descendants=12,
        text=text,
        source_feeds=["top"],
    )


def test_cosine_similarity_identical_vectors_is_one() -> None:
    a = [1.0, 2.0, 3.0]
    b = [1.0, 2.0, 3.0]

    assert cosine_similarity(a, b) == pytest.approx(1.0, rel=1e-6)


def test_cosine_similarity_orthogonal_vectors_is_zero() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]

    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-9)


def test_cosine_similarity_handles_degenerate_vectors() -> None:
    assert cosine_similarity([], [1.0, 2.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], []) == 0.0


@pytest.mark.asyncio
async def test_get_or_create_keyword_embeddings_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = NebiusOpportunityEmbedder(api_key=None)
    calls: list[list[str]] = []

    async def fake_embed_many(
        self: NebiusOpportunityEmbedder, texts: list[str]
    ) -> list[list[float]]:
        calls.append(list(texts))
        vectors: list[list[float]] = []
        for i, _ in enumerate(texts, start=1):
            vectors.append([float(i), 1.0, 0.5])
        return vectors

    monkeypatch.setattr(NebiusOpportunityEmbedder, "embed_many", fake_embed_many)

    first = await embedder._get_or_create_keyword_embeddings(["hiring", "remote"])
    second = await embedder._get_or_create_keyword_embeddings(["hiring", "remote"])
    third = await embedder._get_or_create_keyword_embeddings(["hiring", "contract"])

    assert len(first) == 2
    assert len(second) == 2
    assert len(third) == 2
    assert calls == [["hiring", "remote"], ["contract"]]


@pytest.mark.asyncio
async def test_get_or_create_keyword_embeddings_invalidates_cache_when_model_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = NebiusOpportunityEmbedder(api_key=None)
    calls: list[list[str]] = []

    async def fake_embed_many(
        self: NebiusOpportunityEmbedder, texts: list[str]
    ) -> list[list[float]]:
        calls.append(list(texts))
        vectors: list[list[float]] = []
        for i, _ in enumerate(texts, start=1):
            vectors.append([float(i), 0.0])
        return vectors

    monkeypatch.setattr(NebiusOpportunityEmbedder, "embed_many", fake_embed_many)

    await embedder._get_or_create_keyword_embeddings(["hiring"])
    embedder.model = "another-model"
    await embedder._get_or_create_keyword_embeddings(["hiring"])

    assert calls == [["hiring"], ["hiring"]]


@pytest.mark.asyncio
async def test_rank_stories_against_keywords_returns_empty_without_client() -> None:
    embedder = NebiusOpportunityEmbedder(api_key=None)
    stories = [build_story(story_id=1)]

    result = await embedder.rank_stories_against_keywords(
        stories,
        ["hiring", "remote"],
        min_similarity=0.1,
    )

    assert result == {}
