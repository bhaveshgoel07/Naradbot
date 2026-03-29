from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from html import unescape
from typing import Protocol

from openai import AsyncOpenAI

from personal_agent.hn.models import RankedStory

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StorySummaryResult:
    """Structured summary output used by digest assembly."""

    summary: str
    why_it_matters: str


class StorySummaryProvider(Protocol):
    """Contract for generating shortlist summaries."""

    async def summarize(
        self, ranked_story: RankedStory, channel_key: str
    ) -> StorySummaryResult:
        raise NotImplementedError


class HeuristicStorySummaryProvider:
    """Fast fallback summarizer that requires no external LLM call."""

    async def summarize(
        self, ranked_story: RankedStory, channel_key: str
    ) -> StorySummaryResult:
        if (
            ranked_story.generated_summary is not None
            and ranked_story.generated_why_it_matters is not None
        ):
            return StorySummaryResult(
                summary=ranked_story.generated_summary,
                why_it_matters=ranked_story.generated_why_it_matters,
            )
        story = ranked_story.story
        summary = (
            self._clean_text(story.text)
            if story.text
            else self._default_summary(ranked_story)
        )
        why_it_matters = self._why_it_matters(ranked_story, channel_key)
        return StorySummaryResult(summary=summary, why_it_matters=why_it_matters)

    @staticmethod
    def _clean_text(text: str) -> str:
        normalized = (
            unescape(text)
            .replace("<p>", " ")
            .replace("</p>", " ")
            .replace("<i>", "")
            .replace("</i>", "")
        )
        cleaned = " ".join(normalized.split())
        return cleaned[:260] + ("..." if len(cleaned) > 260 else "")

    @staticmethod
    def _default_summary(ranked_story: RankedStory) -> str:
        story = ranked_story.story
        return f"{story.title} is trending on Hacker News with {story.score} points and {story.descendants} comments."

    @staticmethod
    def _why_it_matters(ranked_story: RankedStory, channel_key: str) -> str:
        if channel_key == "opportunities":
            tags = (
                ", ".join(ranked_story.opportunity_reason_tags[:4])
                or "explicit opportunity match"
            )
            return f"Matched opportunity signals: {tags}."
        if channel_key == "interesting":
            tags = (
                ", ".join(ranked_story.interesting_reason_tags[:4])
                or "overall:high-signal"
            )
            return f"Matched worth-reading signals: {tags}."
        tags = (
            ", ".join(
                (
                    ranked_story.summary_reason_tags
                    + ranked_story.interesting_reason_tags
                    + ranked_story.opportunity_reason_tags
                )[:4]
            )
            or "overall:high-signal"
        )
        return f"Selected as a notable overall story because of: {tags}."


@dataclass(slots=True)
class NebiusStorySummaryProvider:
    """OpenAI-compatible summarizer backed by Nebius."""

    model: str
    base_url: str
    api_key: str | None
    fallback_provider: HeuristicStorySummaryProvider
    _client: AsyncOpenAI | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self._client = (
            AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
            if self.api_key
            else None
        )

    async def summarize(
        self, ranked_story: RankedStory, channel_key: str
    ) -> StorySummaryResult:
        if (
            ranked_story.generated_summary is not None
            and ranked_story.generated_why_it_matters is not None
        ):
            return StorySummaryResult(
                summary=ranked_story.generated_summary,
                why_it_matters=ranked_story.generated_why_it_matters,
            )
        if self._client is None:
            logger.warning(
                "Nebius API key missing; falling back to heuristic summaries"
            )
            return await self.fallback_provider.summarize(ranked_story, channel_key)

        story = ranked_story.story
        prompt = self._build_prompt(ranked_story, channel_key)
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._system_prompt_for(channel_key),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            }
                        ],
                    },
                ],
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Nebius summarization failed for story %s: %s", story.id, exc
            )
            return await self.fallback_provider.summarize(ranked_story, channel_key)

        content = response.choices[0].message.content or ""
        parsed = self._parse_response(content)
        if parsed is None:
            logger.warning(
                "Nebius returned non-JSON summary for story %s; using heuristic fallback",
                story.id,
            )
            return await self.fallback_provider.summarize(ranked_story, channel_key)
        return parsed

    def _system_prompt_for(self, channel_key: str) -> str:
        base_rules = (
            "You produce concise Hacker News digest entries for Discord.\n"
            "Output must be strict JSON with exactly two keys: summary, why_it_matters.\n"
            "No markdown, no code fences, no extra keys, no preamble.\n"
            "Keep language specific and factual. Avoid hype and generic phrases.\n"
            "If data is missing, be transparent and concise."
        )

        if channel_key == "summary":
            return (
                f"{base_rules}\n"
                "Channel objective: HN summary rollup.\n"
                "- summary: one sentence (<= 220 chars) describing what the linked story is about.\n"
                "- why_it_matters: one sentence (<= 180 chars) explaining broader relevance to engineers/builders.\n"
                "Prefer technical impact, ecosystem shifts, or practical implications."
            )
        if channel_key == "interesting":
            return (
                f"{base_rules}\n"
                "Channel objective: read-list / worth-reading selection.\n"
                "- summary: one sentence (<= 220 chars) stating the core idea/result.\n"
                "- why_it_matters: one sentence (<= 180 chars) focused on why a technical reader should spend time on it now.\n"
                "Emphasize novelty, insight density, benchmark value, or implementation detail."
            )
        if channel_key == "opportunities":
            return (
                f"{base_rules}\n"
                "Channel objective: opportunities (hiring, contract, freelance, internships, calls-for-collab).\n"
                "- summary: one sentence (<= 220 chars) describing the opportunity and context.\n"
                "- why_it_matters: one sentence (<= 180 chars) stating role fit, constraints, and urgency when available.\n"
                "Prioritize role clarity, seniority hints, remote/on-site signals, and actionable relevance."
            )

        return (
            f"{base_rules}\n"
            "Channel objective: general technical digest.\n"
            "- summary: one sentence (<= 220 chars).\n"
            "- why_it_matters: one sentence (<= 180 chars)."
        )

    def _build_prompt(self, ranked_story: RankedStory, channel_key: str) -> str:
        story = ranked_story.story
        channel_hint = {
            "summary": "Provide a crisp general-summary digest entry.",
            "interesting": "Frame this for a curated read-list audience.",
            "opportunities": "Frame this as a practical opportunity signal.",
        }.get(channel_key, "Provide a concise technical digest entry.")

        return (
            f"Channel: {channel_key}\n"
            f"Channel hint: {channel_hint}\n"
            f"Title: {story.title}\n"
            f"Author: {story.by}\n"
            f"Score: {story.score}\n"
            f"Comments: {story.descendants}\n"
            f"Domain: {story.domain}\n"
            f"URL: {story.url or story.permalink}\n"
            f"Reason tags: {', '.join(ranked_story.reason_tags[:8])}\n"
            f"Interesting tags: {', '.join(ranked_story.interesting_reason_tags[:8]) or 'none'}\n"
            f"Opportunity tags: {', '.join(ranked_story.opportunity_reason_tags[:8]) or 'none'}\n"
            f"Story text: {story.text or 'No self text provided.'}\n\n"
            "Return JSON only."
        )

    @staticmethod
    def _parse_response(content: str) -> StorySummaryResult | None:
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
        return StorySummaryResult(summary=summary, why_it_matters=why_it_matters)
