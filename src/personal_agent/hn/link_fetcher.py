from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

import aiohttp


@dataclass(slots=True)
class LinkSnapshot:
    """Normalized linked-page snapshot used for summary verification."""

    requested_url: str
    final_url: str
    status_code: int | None
    title: str | None
    excerpt: str
    fetched: bool
    error: str | None = None


class LinkContentFetcher:
    """Fetches and lightly sanitizes linked article pages for LLM grounding."""

    def __init__(self, *, timeout_seconds: int = 20, char_limit: int = 6000) -> None:
        self.timeout_seconds = timeout_seconds
        self.char_limit = char_limit

    async def fetch(self, url: str | None) -> LinkSnapshot | None:
        if not url:
            return None

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers={
                        "User-Agent": (
                            "personal-agent/0.1 (+https://news.ycombinator.com/)"
                        )
                    },
                ) as response:
                    body = await response.text(errors="ignore")
                    cleaned = self._extract_text(body)
                    return LinkSnapshot(
                        requested_url=url,
                        final_url=str(response.url),
                        status_code=response.status,
                        title=self._extract_title(body),
                        excerpt=cleaned[: self.char_limit],
                        fetched=response.status < 400,
                        error=None if response.status < 400 else f"http_{response.status}",
                    )
        except Exception as exc:  # noqa: BLE001
            return LinkSnapshot(
                requested_url=url,
                final_url=url,
                status_code=None,
                title=None,
                excerpt="",
                fetched=False,
                error=str(exc),
            )

    @staticmethod
    def _extract_title(html: str) -> str | None:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            return None
        return " ".join(unescape(match.group(1)).split()) or None

    @staticmethod
    def _extract_text(html: str) -> str:
        without_scripts = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        without_comments = re.sub(r"<!--.*?-->", " ", without_scripts, flags=re.DOTALL)
        without_tags = re.sub(r"<[^>]+>", " ", without_comments)
        normalized = unescape(without_tags)
        return " ".join(normalized.split())
