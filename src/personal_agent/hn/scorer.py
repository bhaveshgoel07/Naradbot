from __future__ import annotations

from collections.abc import Iterable

from personal_agent.hn.models import HNStory, RankedStory

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
    """Heuristic ranking layer that keeps LLM usage minimal."""

    def rank_stories(self, stories: Iterable[HNStory]) -> list[RankedStory]:
        ranked: list[RankedStory] = []
        for story in stories:
            interesting_score, interesting_reasons = self._score_interesting(story)
            opportunity_score, opportunity_reasons = self._score_opportunity(story)
            summary_score, summary_reasons = self._score_summary(
                story,
                interesting_score=interesting_score,
                opportunity_score=opportunity_score,
            )
            ranked.append(
                RankedStory(
                    story=story,
                    interesting_score=round(interesting_score, 2),
                    opportunity_score=round(opportunity_score, 2),
                    summary_score=round(summary_score, 2),
                    reason_tags=summary_reasons + interesting_reasons + opportunity_reasons,
                    interesting_reason_tags=interesting_reasons,
                    opportunity_reason_tags=opportunity_reasons,
                    summary_reason_tags=summary_reasons,
                )
            )

        return sorted(ranked, key=lambda item: item.summary_score, reverse=True)

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
        text_blob = self._text_blob(story)
        score = 0.0
        reasons: list[str] = []

        for keyword, weight in OPPORTUNITY_KEYWORDS.items():
            if keyword in text_blob:
                score += weight
                reasons.append(f"keyword:{keyword}")

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
    def _text_blob(story: HNStory) -> str:
        return " ".join(part for part in [story.title, story.text or "", story.domain] if part).lower()

    @staticmethod
    def _domain_bonus(domain: str, weights: dict[str, float]) -> float:
        for known_domain, weight in weights.items():
            if domain.endswith(known_domain):
                return weight
        return 0.0
