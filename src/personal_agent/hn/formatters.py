from __future__ import annotations

from datetime import datetime, timezone

from personal_agent.hn.models import ChannelDigest, DigestEntry


class DiscordDigestFormatter:
    """Creates readable Discord message bodies for channel digests."""

    def format_digest(self, digest: ChannelDigest) -> str:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if not digest.entries and not digest.overview_lines:
            return f"**{digest.title}**\nGenerated at {generated_at}.\nNo new stories matched this digest in the latest run."

        sections = [f"**{digest.title}**", f"Generated at {generated_at}."]
        if digest.overview_lines:
            sections.append(
                "What Hacker News talked about:\n" + "\n".join(f"- {line}" for line in digest.overview_lines)
            )
        if digest.selection_title:
            sections.append(f"**{digest.selection_title}**")
        for index, entry in enumerate(digest.entries, start=1):
            sections.append(self._format_entry(index, entry, digest.channel_key))
        return "\n\n".join(sections)

    def _format_entry(self, index: int, entry: DigestEntry, channel_key: str) -> str:
        story = entry.ranked_story.story
        if channel_key == "opportunities":
            stats = f"By: {story.by} | Domain: {story.domain} | Posted: {story.created_at.strftime('%Y-%m-%d')}"
        else:
            stats = f"Score: {story.score} | Comments: {story.descendants} | By: {story.by} | Domain: {story.domain}"
        link = story.url or story.permalink
        rationale_label = "Why read" if channel_key == "interesting" else "Why it matters"
        return (
            f"{index}. **{story.title}**\n"
            f"{stats}\n"
            f"Summary: {entry.summary}\n"
            f"{rationale_label}: {entry.why_it_matters}\n"
            f"Link: {link}"
        )
