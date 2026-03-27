from __future__ import annotations

import logging
from collections.abc import Iterable

from personal_agent.hn.models import HNStory, RankedStory
from personal_agent.hn.opportunity_embeddings import (
    NebiusOpportunityEmbedder,
    OpportunityEmbeddingScore,
)
from personal_agent.hn.story_analysis import StoryAnalysisProvider

logger = logging.getLogger(__name__)

INTERESTING_KEYWORDS: dict[str, float] = {
    "open source": 2.5,
    "launch": 1.4,
    "research": 2.1,
    "benchmark": 1.8,
    "ai": 1.6,
    "agent": 1.8,
    "infra": 1.5,
}

OPPORTUNITY_KEYWORDS: dict[str, float] = {
    "hiring": 4.0,
    "job": 2.0,
    "jobs": 2.0,
    "founding engineer": 4.5,
    "contract": 3.2,
    "freelance": 3.2,
    "remote": 2.2,
    "bounty": 3.0,
    "intern": 2.8,
    "internship": 3.2,
    "who is hiring": 5.0,
    "who wants to be hired": 4.0,
}

OPPORTUNITY_JOB_POST_QUERIES: tuple[str, ...] = (
    "A real job post with an open role and a request for candidates to apply.",
    "A hiring page describing a specific engineering role, team, location, or compensation.",
    "A contract, freelance, internship, or full-time role announcement with application intent.",
)

INTERESTING_DOMAINS: dict[str, float] = {
    "github.com": 2.3,
    "arxiv.org": 2.4,
    "huggingface.co": 2.0,
    "openai.com": 1.6,
    "anthropic.com": 1.3,
    "stripe.com": 1.0,
}

OPPORTUNITY_DOMAINS: dict[str, float] = {
    "jobs.lever.co": 3.5,
    "boards.greenhouse.io": 3.5,
    "wellfound.com": 3.5,
}

NOISE_KEYWORDS: dict[str, float] = {
    "ask hn": -0.5,
    "tell hn": -0.2,
}


class StoryScorer:
    """Heuristic ranking with optional embedding validation and LLM analysis."""

    def __init__(
        self,
        *,
        opportunity_embedder: NebiusOpportunityEmbedder | None = None,
        story_analysis_provider: StoryAnalysisProvider | None = None,
        job_post_queries: tuple[str, ...] | list[str] | None = None,
        hiring_keywords: tuple[str, ...] | list[str] | None = None,
        non_job_keywords: tuple[str, ...] | list[str] | None = None,
        opportunity_min_similarity: float = 0.45,
        opportunity_min_margin: float = 0.03,
    ) -> None:
        self.opportunity_embedder = opportunity_embedder
        self.story_analysis_provider = story_analysis_provider
        selected_queries = job_post_queries or hiring_keywords
        self.job_post_queries = list(
            selected_queries or OPPORTUNITY_JOB_POST_QUERIES
        )
        self.non_job_keywords = list(non_job_keywords or [])
        self.opportunity_min_similarity = opportunity_min_similarity
        self.opportunity_min_margin = opportunity_min_margin

    def rank_stories(self, stories: Iterable[HNStory]) -> list[RankedStory]:
        """Synchronous heuristic ranking (no remote embedding calls)."""
        return self._rank_stories_with_metadata(stories)[0]

    def rank_stories_with_metadata(
        self, stories: Iterable[HNStory]
    ) -> tuple[list[RankedStory], dict[str, object]]:
        """Synchronous heuristic ranking plus scoring metadata."""
        ranked, metadata = self._rank_stories_with_metadata(stories)
        return ranked, metadata

    async def rank_stories_async(self, stories: Iterable[HNStory]) -> list[RankedStory]:
        """Async ranking with optional embedding and LLM enrichment."""
        ranked, _, _ = await self.rank_stories_async_with_metadata(stories)
        return ranked

    async def rank_stories_async_with_metadata(
        self, stories: Iterable[HNStory]
    ) -> tuple[list[RankedStory], dict[str, object], dict[int, dict[str, object]]]:
        story_list = list(stories)
        ranked, score_metadata = self._rank_stories_with_metadata(story_list)

        ranked, embedding_matches = await self._apply_embedding_validation(
            ranked, score_metadata
        )
        ranked = await self._apply_story_analysis(
            ranked,
            embedding_matches=embedding_matches,
            score_metadata=score_metadata,
        )

        return (
            sorted(ranked, key=lambda item: item.summary_score, reverse=True),
            score_metadata,
            embedding_matches,
        )

    async def _apply_embedding_validation(
        self,
        ranked: list[RankedStory],
        score_metadata: dict[str, object],
    ) -> tuple[list[RankedStory], dict[int, dict[str, object]]]:
        if (
            not ranked
            or self.opportunity_embedder is None
            or not self.opportunity_embedder.enabled
            or not self.job_post_queries
        ):
            score_metadata["embedding_enabled"] = False
            score_metadata["embedding_match_count"] = 0
            return ranked, {}

        try:
            embedding_hits = await self.opportunity_embedder.rank_stories_against_keywords(
                [item.story for item in ranked],
                self.job_post_queries,
                non_job_keywords=self.non_job_keywords,
                min_similarity=self.opportunity_min_similarity,
                min_margin=self.opportunity_min_margin,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Embedding-based opportunity ranking failed; using heuristics only: %s",
                exc,
            )
            score_metadata["embedding_enabled"] = True
            score_metadata["embedding_error"] = str(exc)
            score_metadata["embedding_match_count"] = 0
            return ranked, {}

        embedding_matches: dict[int, dict[str, object]] = {}
        verified_ranked: list[RankedStory] = []
        for item in ranked:
            hit = embedding_hits.get(item.story.id)
            explicit_thread = item.story.title.lower().startswith("who is hiring")

            if hit is None and not explicit_thread:
                verified_ranked.append(
                    RankedStory(
                        story=item.story,
                        interesting_score=item.interesting_score,
                        opportunity_score=0.0,
                        summary_score=item.summary_score,
                        generated_summary=item.generated_summary,
                        generated_why_it_matters=item.generated_why_it_matters,
                        analysis_source=item.analysis_source,
                        verification_status=item.verification_status,
                        verification_notes=item.verification_notes,
                        opportunity_verified=False,
                        reason_tags=list(item.reason_tags),
                        interesting_reason_tags=list(item.interesting_reason_tags),
                        opportunity_reason_tags=list(item.opportunity_reason_tags),
                        summary_reason_tags=list(item.summary_reason_tags),
                    )
                )
                continue

            if hit is None:
                verified_ranked.append(
                    RankedStory(
                        story=item.story,
                        interesting_score=item.interesting_score,
                        opportunity_score=item.opportunity_score,
                        summary_score=item.summary_score,
                        generated_summary=item.generated_summary,
                        generated_why_it_matters=item.generated_why_it_matters,
                        analysis_source=item.analysis_source,
                        verification_status=item.verification_status,
                        verification_notes=item.verification_notes,
                        opportunity_verified=True,
                        reason_tags=list(item.reason_tags),
                        interesting_reason_tags=list(item.interesting_reason_tags),
                        opportunity_reason_tags=list(item.opportunity_reason_tags),
                        summary_reason_tags=list(item.summary_reason_tags),
                    )
                )
                continue

            boosted_item = self._apply_embedding_opportunity_boost(item, hit)
            verified_ranked.append(boosted_item)
            embedding_matches[item.story.id] = {
                "similarity": hit.similarity,
                "negative_similarity": hit.negative_similarity,
                "margin": hit.margin,
                "matched_keyword": hit.matched_keyword,
                "matched_negative_keyword": hit.matched_negative_keyword,
                "matched_keyword_similarity": hit.matched_keyword_similarity,
                "channel_tags": list(hit.channel_tags),
            }

        score_metadata["embedding_enabled"] = True
        score_metadata["embedding_match_count"] = len(embedding_matches)
        score_metadata["embedding_similarity_threshold"] = (
            self.opportunity_min_similarity
        )
        score_metadata["embedding_margin_threshold"] = self.opportunity_min_margin
        score_metadata["embedding_query_count"] = len(self.job_post_queries)
        score_metadata["verified_opportunity_count"] = sum(
            1 for item in verified_ranked if item.is_opportunity
        )
        return verified_ranked, embedding_matches

    async def _apply_story_analysis(
        self,
        ranked: list[RankedStory],
        *,
        embedding_matches: dict[int, dict[str, object]],
        score_metadata: dict[str, object],
    ) -> list[RankedStory]:
        if (
            not ranked
            or self.story_analysis_provider is None
            or not self.story_analysis_provider.enabled
        ):
            score_metadata["analysis_enabled"] = False
            return ranked

        try:
            analyses = await self.story_analysis_provider.analyze_many(
                ranked,
                embedding_matches=embedding_matches,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Story analysis failed; keeping heuristic ranking: %s", exc)
            score_metadata["analysis_enabled"] = True
            score_metadata["analysis_error"] = str(exc)
            return ranked

        if not analyses:
            score_metadata["analysis_enabled"] = True
            score_metadata["analysis_story_count"] = 0
            return ranked

        analyzed_ranked: list[RankedStory] = []
        verified_summary_count = 0
        for item in ranked:
            analysis = analyses.get(item.story.id)
            if analysis is None:
                analyzed_ranked.append(item)
                continue

            if analysis.verification_status == "verified":
                verified_summary_count += 1

            llm_opportunity_score = analysis.opportunity_score
            if self.opportunity_embedder is not None and not item.opportunity_verified:
                llm_opportunity_score = 0.0

            analyzed_ranked.append(
                RankedStory(
                    story=item.story,
                    interesting_score=self._blend_score(
                        analysis.interesting_score,
                        item.interesting_score,
                    ),
                    opportunity_score=self._blend_score(
                        llm_opportunity_score,
                        item.opportunity_score,
                        llm_weight=0.85,
                    ),
                    summary_score=self._blend_score(
                        analysis.summary_score,
                        item.summary_score,
                    ),
                    generated_summary=analysis.summary,
                    generated_why_it_matters=analysis.why_it_matters,
                    analysis_source="llm_story_analysis",
                    verification_status=analysis.verification_status,
                    verification_notes=analysis.verification_notes,
                    opportunity_verified=item.opportunity_verified,
                    reason_tags=list(item.reason_tags),
                    interesting_reason_tags=list(item.interesting_reason_tags),
                    opportunity_reason_tags=list(item.opportunity_reason_tags),
                    summary_reason_tags=list(item.summary_reason_tags),
                )
            )

        score_metadata["analysis_enabled"] = True
        score_metadata["analysis_story_count"] = len(analyses)
        score_metadata["analysis_verified_summary_count"] = verified_summary_count
        return analyzed_ranked

    def _rank_stories_with_metadata(
        self, stories: Iterable[HNStory]
    ) -> tuple[list[RankedStory], dict[str, object]]:
        ranked: list[RankedStory] = []
        story_count = 0
        interesting_positive = 0
        opportunity_positive = 0

        for story in stories:
            story_count += 1
            interesting_score, interesting_reasons = self._score_interesting(story)
            opportunity_score, opportunity_reasons = self._score_opportunity(story)
            summary_score, summary_reasons = self._score_summary(
                story,
                interesting_score=interesting_score,
                opportunity_score=opportunity_score,
            )
            if interesting_score > 0:
                interesting_positive += 1
            if opportunity_score > 0:
                opportunity_positive += 1
            ranked.append(
                RankedStory(
                    story=story,
                    interesting_score=round(interesting_score, 2),
                    opportunity_score=round(opportunity_score, 2),
                    summary_score=round(summary_score, 2),
                    reason_tags=summary_reasons
                    + interesting_reasons
                    + opportunity_reasons,
                    interesting_reason_tags=interesting_reasons,
                    opportunity_reason_tags=opportunity_reasons,
                    summary_reason_tags=summary_reasons,
                )
            )

        sorted_ranked = sorted(
            ranked, key=lambda item: item.summary_score, reverse=True
        )
        metadata: dict[str, object] = {
            "story_count": story_count,
            "interesting_positive_count": interesting_positive,
            "opportunity_positive_count": opportunity_positive,
            "embedding_enabled": False,
            "analysis_enabled": False,
        }
        return sorted_ranked, metadata

    def _apply_embedding_opportunity_boost(
        self,
        ranked_story: RankedStory,
        hit: OpportunityEmbeddingScore,
    ) -> RankedStory:
        similarity = max(0.0, min(hit.similarity, 1.0))
        similarity_boost = similarity * 6.0

        opportunity_score = round(ranked_story.opportunity_score + similarity_boost, 2)
        summary_score = round(ranked_story.summary_score + (similarity_boost * 0.2), 2)

        opportunity_reasons = list(ranked_story.opportunity_reason_tags)
        opportunity_reasons.extend(
            [
                "embedding:job-post",
                f"embedding:keyword:{hit.matched_keyword}",
                f"embedding:cosine:{hit.matched_keyword_similarity}",
                f"embedding:margin:{hit.margin}",
            ]
        )
        deduped_opportunity_reasons = list(dict.fromkeys(opportunity_reasons))
        reason_tags = list(
            dict.fromkeys(ranked_story.reason_tags + deduped_opportunity_reasons)
        )

        return RankedStory(
            story=ranked_story.story,
            interesting_score=ranked_story.interesting_score,
            opportunity_score=opportunity_score,
            summary_score=summary_score,
            generated_summary=ranked_story.generated_summary,
            generated_why_it_matters=ranked_story.generated_why_it_matters,
            analysis_source=ranked_story.analysis_source,
            verification_status=ranked_story.verification_status,
            verification_notes=ranked_story.verification_notes,
            opportunity_verified=True,
            reason_tags=reason_tags,
            interesting_reason_tags=list(ranked_story.interesting_reason_tags),
            opportunity_reason_tags=deduped_opportunity_reasons,
            summary_reason_tags=list(ranked_story.summary_reason_tags),
        )

    def _score_interesting(self, story: HNStory) -> tuple[float, list[str]]:
        text_blob = self._text_blob(story)
        score = min(story.score / 40.0, 4.0)
        reasons = [f"score:{story.score}"] if story.score else []

        comment_bonus = min(story.descendants / 25.0, 3.0)
        if comment_bonus > 0:
            score += comment_bonus
            reasons.append(f"comments:{story.descendants}")

        domain_bonus = self._domain_bonus(story.domain, INTERESTING_DOMAINS)
        if domain_bonus > 0:
            score += domain_bonus
            reasons.append(f"domain:{story.domain}")

        for keyword, weight in INTERESTING_KEYWORDS.items():
            if keyword in text_blob:
                score += weight
                reasons.append(f"keyword:{keyword}")

        for keyword, penalty in NOISE_KEYWORDS.items():
            if keyword in text_blob and keyword != "ask hn":
                score += penalty
                reasons.append(f"noise:{keyword}")

        return score, reasons

    def _score_opportunity(self, story: HNStory) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        domain_bonus = self._domain_bonus(story.domain, OPPORTUNITY_DOMAINS)
        if domain_bonus > 0:
            score += domain_bonus
            reasons.append(f"domain:{story.domain}")

        if story.title.lower().startswith("who is hiring"):
            score += 2.0
            reasons.append("thread:who-is-hiring")

        return score, reasons

    def _score_summary(
        self,
        story: HNStory,
        *,
        interesting_score: float,
        opportunity_score: float,
    ) -> tuple[float, list[str]]:
        score = min(story.score / 35.0, 5.0)
        score += min(story.descendants / 30.0, 3.0)
        score += interesting_score * 0.35
        score += opportunity_score * 0.2
        reasons = ["overall:high-signal"]
        return score, reasons

    @staticmethod
    def _blend_score(
        llm_score: float,
        heuristic_score: float,
        *,
        llm_weight: float = 0.75,
    ) -> float:
        heuristic_weight = max(0.0, 1.0 - llm_weight)
        return round((llm_score * llm_weight) + (heuristic_score * heuristic_weight), 2)

    @staticmethod
    def _text_blob(story: HNStory) -> str:
        return " ".join(
            part for part in [story.title, story.text or "", story.domain] if part
        ).lower()

    @staticmethod
    def _domain_bonus(domain: str, weights: dict[str, float]) -> float:
        for known_domain, weight in weights.items():
            if domain.endswith(known_domain):
                return weight
        return 0.0
