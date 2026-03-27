from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from openai import AsyncOpenAI

from personal_agent.hn.link_fetcher import LinkContentFetcher, LinkSnapshot
from personal_agent.hn.models import RankedStory

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StoryAnalysisResult:
    """LLM-generated triage metadata for a single Hacker News story."""

    interesting_score: float
    summary_score: float
    opportunity_score: float
    summary: str
    why_it_matters: str
    verification_status: str
    verification_notes: str


class StoryAnalysisProvider(Protocol):
    """Contract for all-story triage, scoring, and verification."""

    @property
    def enabled(self) -> bool:
        raise NotImplementedError

    async def analyze_many(
        self,
        ranked_stories: list[RankedStory],
        *,
        embedding_matches: dict[int, dict[str, object]],
    ) -> dict[int, StoryAnalysisResult]:
        raise NotImplementedError


@dataclass(slots=True)
class NebiusStoryAnalysisProvider:
    """Small-model analysis provider for scoring, summarization, and link checks."""

    model: str
    base_url: str
    api_key: str | None
    link_fetcher: LinkContentFetcher
    concurrency_limit: int = 4
    verify_links: bool = True
    _client: AsyncOpenAI | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = (
            AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
            if self.api_key
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def analyze_many(
        self,
        ranked_stories: list[RankedStory],
        *,
        embedding_matches: dict[int, dict[str, object]],
    ) -> dict[int, StoryAnalysisResult]:
        if self._client is None or not ranked_stories:
            return {}

        semaphore = asyncio.Semaphore(max(1, self.concurrency_limit))
        tasks = [
            asyncio.create_task(
                self._analyze_one(
                    semaphore,
                    ranked_story,
                    embedding_matches.get(ranked_story.story.id),
                )
            )
            for ranked_story in ranked_stories
        ]

        analyzed: dict[int, StoryAnalysisResult] = {}
        for story_id, result in await asyncio.gather(*tasks):
            if result is not None:
                analyzed[story_id] = result
        return analyzed

    async def _analyze_one(
        self,
        semaphore: asyncio.Semaphore,
        ranked_story: RankedStory,
        embedding_match: dict[str, object] | None,
    ) -> tuple[int, StoryAnalysisResult | None]:
        async with semaphore:
            story = ranked_story.story
            link_snapshot = (
                await self.link_fetcher.fetch(story.url)
                if self.verify_links and story.url
                else None
            )
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": self._system_prompt(),
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": self._build_prompt(
                                        ranked_story,
                                        embedding_match=embedding_match,
                                        link_snapshot=link_snapshot,
                                    ),
                                }
                            ],
                        },
                    ],
                    temperature=0.1,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Story analysis failed for story %s: %s", story.id, exc)
                return story.id, None

        content = response.choices[0].message.content or ""
        return story.id, self._parse_response(content, link_snapshot=link_snapshot)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You analyze Hacker News posts for three Discord digests: summary, interesting, and opportunities.\n"
            "Return strict JSON with exactly these keys: interesting_score, summary_score, opportunity_score, "
            "summary, why_it_matters, verified_against_link, verification_notes.\n"
            "Scores must be numbers from 0 to 10.\n"
            "Use the linked page as the primary source when available.\n"
            "Opportunity score should be high only for actual roles, contracts, internships, bounties, or concrete collaboration calls. "
            "General discussion about hiring or careers should score near zero.\n"
            "Keep summary <= 220 characters and why_it_matters <= 180 characters.\n"
            "If link content is missing, say so in verification_notes and rely on the Hacker News post."
        )

    @staticmethod
    def _build_prompt(
        ranked_story: RankedStory,
        *,
        embedding_match: dict[str, object] | None,
        link_snapshot: LinkSnapshot | None,
    ) -> str:
        story = ranked_story.story
        link_section = "Linked page was not fetched."
        if link_snapshot is not None:
            link_section = (
                f"Link fetched: {link_snapshot.fetched}\n"
                f"Final URL: {link_snapshot.final_url}\n"
                f"HTTP status: {link_snapshot.status_code}\n"
                f"Page title: {link_snapshot.title or 'none'}\n"
                f"Page excerpt: {link_snapshot.excerpt or 'none'}\n"
                f"Link fetch error: {link_snapshot.error or 'none'}"
            )

        embedding_section = "none"
        if embedding_match is not None:
            embedding_section = json.dumps(embedding_match, sort_keys=True)

        return (
            f"Title: {story.title}\n"
            f"Author: {story.by}\n"
            f"Score: {story.score}\n"
            f"Comments: {story.descendants}\n"
            f"Domain: {story.domain}\n"
            f"URL: {story.url or story.permalink}\n"
            f"HN text: {story.text or 'No self text provided.'}\n"
            f"Heuristic interesting score: {ranked_story.interesting_score}\n"
            f"Heuristic summary score: {ranked_story.summary_score}\n"
            f"Heuristic opportunity score: {ranked_story.opportunity_score}\n"
            f"Reason tags: {', '.join(ranked_story.reason_tags[:10]) or 'none'}\n"
            f"Opportunity embedding match: {embedding_section}\n\n"
            f"{link_section}\n\n"
            "Return JSON only."
        )

    @staticmethod
    def _parse_response(
        content: str, *, link_snapshot: LinkSnapshot | None
    ) -> StoryAnalysisResult | None:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None

        summary = str(payload.get("summary", "")).strip()
        why_it_matters = str(payload.get("why_it_matters", "")).strip()
        if not summary or not why_it_matters:
            return None

        verified_against_link = bool(payload.get("verified_against_link"))
        verification_status = (
            "verified"
            if verified_against_link
            else ("needs_review" if link_snapshot is not None else "not_checked")
        )

        return StoryAnalysisResult(
            interesting_score=StoryAnalysisResultParser.score(
                payload.get("interesting_score", 0.0)
            ),
            summary_score=StoryAnalysisResultParser.score(
                payload.get("summary_score", 0.0)
            ),
            opportunity_score=StoryAnalysisResultParser.score(
                payload.get("opportunity_score", 0.0)
            ),
            summary=summary,
            why_it_matters=why_it_matters,
            verification_status=verification_status,
            verification_notes=str(payload.get("verification_notes", "")).strip()
            or ("Link not checked." if link_snapshot is None else "Needs manual review."),
        )


class StoryAnalysisResultParser:
    @staticmethod
    def score(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        return max(0.0, min(numeric, 10.0))
