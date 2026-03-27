from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

from openai import AsyncOpenAI

from personal_agent.hn.models import HNStory

logger = logging.getLogger(__name__)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Return cosine similarity in [-1, 1], or 0.0 when vectors are degenerate."""
    if not a or not b:
        return 0.0
    denominator = _norm(a) * _norm(b)
    if denominator <= 0:
        return 0.0
    return _dot(a, b) / denominator


@dataclass(slots=True)
class OpportunityEmbeddingScore:
    """Result of embedding-based opportunity matching for one story."""

    story_id: int
    similarity: float
    negative_similarity: float
    margin: float
    matched_keyword: str
    matched_keyword_similarity: float
    matched_negative_keyword: str
    channel_tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NebiusOpportunityEmbedder:
    """
    Nebius embedding helper for opportunity detection.

    Uses an OpenAI-compatible endpoint:
    - base_url: e.g. https://api.tokenfactory.nebius.com/v1/
    - model: e.g. Qwen/Qwen3-Embedding-8B
    """

    model: str = "Qwen/Qwen3-Embedding-8B"
    base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    api_key: str | None = None
    _client: AsyncOpenAI | None = field(init=False, default=None, repr=False)
    _keyword_embedding_cache: dict[str, list[float]] = field(
        init=False, default_factory=dict, repr=False
    )
    _keyword_cache_model: str | None = field(init=False, default=None, repr=False)
    _keyword_cache_base_url: str | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = (
            AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
            if self.api_key
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def embed_text(self, text: str) -> list[float]:
        """
        Create a single embedding vector for text.

        Raises RuntimeError if API key is missing, so callers can fallback gracefully.
        """
        if self._client is None:
            raise RuntimeError(
                "Nebius embedding client is not configured (missing API key)."
            )

        response = await self._client.embeddings.create(
            model=self.model,
            input=text,
        )
        # OpenAI-compatible response shape: response.data[0].embedding
        return list(response.data[0].embedding)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Batch embed many texts in one API call."""
        if self._client is None:
            raise RuntimeError(
                "Nebius embedding client is not configured (missing API key)."
            )
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]

    @staticmethod
    def story_to_embedding_text(story: HNStory) -> str:
        """Canonical text representation to embed for opportunity matching."""
        title = story.title.strip()
        body = (story.text or "").strip()
        domain = story.domain.strip()
        url = (story.url or story.permalink).strip()
        return (
            f"Title: {title}\n"
            f"Domain: {domain}\n"
            f"URL: {url}\n"
            f"Body: {body if body else 'No self text provided.'}"
        )

    async def rank_stories_against_keywords(
        self,
        stories: list[HNStory],
        hiring_keywords: list[str],
        *,
        non_job_keywords: list[str] | None = None,
        min_similarity: float = 0.45,
        min_margin: float = 0.03,
    ) -> dict[int, OpportunityEmbeddingScore]:
        """
        Embed stories + semantic job-post queries and return story_id -> best match for those >= min_similarity.

        Strategy:
        1. Embed all positive opportunity queries once.
        2. Embed each story text.
        3. Compute max cosine similarity(story, query_embedding).
        """
        if not stories or not hiring_keywords:
            return {}
        if self._client is None:
            logger.warning(
                "Nebius embedding API key missing; skipping embedding-based opportunity ranking"
            )
            return {}

        keyword_embeddings = await self._get_or_create_keyword_embeddings(
            hiring_keywords
        )
        negative_keywords = list(non_job_keywords or [])
        negative_keyword_embeddings = await self._get_or_create_keyword_embeddings(
            negative_keywords
        )
        story_texts = [self.story_to_embedding_text(story) for story in stories]
        story_embeddings = await self.embed_many(story_texts)

        results: dict[int, OpportunityEmbeddingScore] = {}
        for story, story_embedding in zip(stories, story_embeddings, strict=True):
            best_keyword = ""
            best_similarity = -1.0
            best_negative_keyword = ""
            best_negative_similarity = -1.0

            for keyword, keyword_embedding in zip(
                hiring_keywords, keyword_embeddings, strict=True
            ):
                similarity = cosine_similarity(story_embedding, keyword_embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_keyword = keyword

            for keyword, keyword_embedding in zip(
                negative_keywords, negative_keyword_embeddings, strict=True
            ):
                similarity = cosine_similarity(story_embedding, keyword_embedding)
                if similarity > best_negative_similarity:
                    best_negative_similarity = similarity
                    best_negative_keyword = keyword

            margin = best_similarity - max(best_negative_similarity, 0.0)
            if best_similarity >= min_similarity and margin >= min_margin:
                results[story.id] = OpportunityEmbeddingScore(
                    story_id=story.id,
                    similarity=round(best_similarity, 4),
                    negative_similarity=round(max(best_negative_similarity, 0.0), 4),
                    margin=round(margin, 4),
                    matched_keyword=best_keyword,
                    matched_keyword_similarity=round(best_similarity, 4),
                    matched_negative_keyword=best_negative_keyword,
                    channel_tags=[
                        "embedding:job-post",
                        f"embedding:keyword:{best_keyword}",
                        f"embedding:cosine:{round(best_similarity, 4)}",
                        f"embedding:margin:{round(margin, 4)}",
                    ],
                )

        return results

    async def _get_or_create_keyword_embeddings(
        self, hiring_keywords: list[str]
    ) -> list[list[float]]:
        """
        Return embeddings for hiring keywords, using an in-memory cache keyed by keyword.
        Cache is scoped to this process/runtime and invalidated if model/base_url changes.
        """
        if not hiring_keywords:
            return []

        if (
            self._keyword_cache_model != self.model
            or self._keyword_cache_base_url != self.base_url
        ):
            self._keyword_embedding_cache.clear()
            self._keyword_cache_model = self.model
            self._keyword_cache_base_url = self.base_url

        missing_keywords = [
            keyword
            for keyword in hiring_keywords
            if keyword not in self._keyword_embedding_cache
        ]
        if missing_keywords:
            missing_embeddings = await self.embed_many(missing_keywords)
            for keyword, embedding in zip(
                missing_keywords, missing_embeddings, strict=True
            ):
                self._keyword_embedding_cache[keyword] = embedding

        return [self._keyword_embedding_cache[keyword] for keyword in hiring_keywords]
