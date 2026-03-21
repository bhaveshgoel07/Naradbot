from __future__ import annotations

import re
from dataclasses import dataclass

from personal_agent.hn.models import RankedStory


@dataclass(frozen=True, slots=True)
class TitleTheme:
    label: str
    keywords: tuple[str, ...]


TITLE_THEMES: tuple[TitleTheme, ...] = (
    TitleTheme(
        label="AI models, agents, and tooling",
        keywords=("ai", "llm", "model", "agent", "agents", "gpt", "claude", "prompt", "inference", "rag"),
    ),
    TitleTheme(
        label="developer tools, infrastructure, and databases",
        keywords=("infra", "infrastructure", "database", "postgres", "sqlite", "compiler", "runtime", "docker", "kubernetes", "api"),
    ),
    TitleTheme(
        label="open-source launches and product releases",
        keywords=("open source", "launch", "launched", "release", "released", "oss", "github"),
    ),
    TitleTheme(
        label="programming languages, browsers, and systems work",
        keywords=("python", "rust", "go", "javascript", "typescript", "java", "linux", "kernel", "browser", "wasm"),
    ),
    TitleTheme(
        label="research papers, benchmarks, and science",
        keywords=("research", "paper", "study", "benchmark", "arxiv", "science", "physics", "math", "mathematics"),
    ),
    TitleTheme(
        label="security and privacy",
        keywords=("security", "privacy", "auth", "authentication", "password", "vulnerability", "exploit", "cve", "encryption"),
    ),
    TitleTheme(
        label="startups, YC, and company moves",
        keywords=("startup", "founder", "company", "yc", "acquisition", "pricing", "revenue", "business"),
    ),
    TitleTheme(
        label="hiring and career threads",
        keywords=("hiring", "jobs", "job", "freelance", "contract", "intern", "internship", "career", "role", "roles"),
    ),
)


class TitleRollupBuilder:
    """Compress a batch of story titles into a short 'what was discussed' rollup."""

    def build(self, ranked_stories: list[RankedStory], *, limit: int) -> list[str]:
        if not ranked_stories:
            return []

        unique_story_ids = {ranked_story.story.id for ranked_story in ranked_stories}
        theme_counts: list[tuple[int, str]] = []
        matched_story_ids: set[int] = set()

        for theme in TITLE_THEMES:
            matched_ids = {
                ranked_story.story.id
                for ranked_story in ranked_stories
                if self._matches_theme(ranked_story.story.title, theme)
            }
            if matched_ids:
                theme_counts.append((len(matched_ids), theme.label))
                matched_story_ids.update(matched_ids)

        theme_counts.sort(key=lambda item: (item[0], item[1]), reverse=True)
        clustered_topics = [item for item in theme_counts if item[0] >= 2]
        selected_topics = clustered_topics[:limit] if clustered_topics else theme_counts[: min(limit, 3)]

        if not selected_topics:
            return [
                f"Discussion was fragmented across {len(unique_story_ids)} new stories rather than one dominant topic."
            ]

        lines = [self._format_topic_line(label, count) for count, label in selected_topics]
        unmatched_count = len(unique_story_ids - matched_story_ids)
        if unmatched_count > 0 and len(lines) < limit:
            story_label = "story" if unmatched_count == 1 else "stories"
            lines.append(
                f"The rest was a mix of one-off essays, launches, and niche curiosities ({unmatched_count} {story_label})."
            )
        return lines

    @staticmethod
    def _matches_theme(title: str, theme: TitleTheme) -> bool:
        lowered_title = title.lower()
        return any(TitleRollupBuilder._contains_keyword(lowered_title, keyword) for keyword in theme.keywords)

    @staticmethod
    def _contains_keyword(text: str, keyword: str) -> bool:
        if " " in keyword:
            return keyword in text
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None

    @staticmethod
    def _format_topic_line(label: str, count: int) -> str:
        sentence_label = label[0].upper() + label[1:]
        if count == 1:
            return f"{sentence_label} showed up once."
        return f"{sentence_label} kept coming up across {count} titles."
