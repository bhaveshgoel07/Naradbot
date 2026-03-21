from __future__ import annotations

import re

DISCORD_MESSAGE_CHAR_LIMIT = 2000


def split_discord_message_content(message: str, limit: int = DISCORD_MESSAGE_CHAR_LIMIT) -> list[str]:
    """Split Discord content into chunks that fit the configured character limit."""
    if limit <= 0:
        raise ValueError("Discord message limit must be greater than zero.")
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current_chunk = ""
    paragraphs = re.findall(r".*?(?:\n\n|$)", message, flags=re.DOTALL)

    def flush_current_chunk() -> None:
        nonlocal current_chunk
        if current_chunk:
            chunks.append(current_chunk)
            current_chunk = ""

    def append_token(token: str) -> None:
        nonlocal current_chunk
        if not token:
            return
        if len(current_chunk) + len(token) <= limit:
            current_chunk += token
            return

        flush_current_chunk()
        if len(token) <= limit:
            current_chunk = token
            return

        for line in token.splitlines(keepends=True):
            if len(current_chunk) + len(line) <= limit:
                current_chunk += line
                continue

            flush_current_chunk()
            if len(line) <= limit:
                current_chunk = line
                continue

            for start in range(0, len(line), limit):
                part = line[start : start + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current_chunk = part

    for paragraph in paragraphs:
        append_token(paragraph)

    flush_current_chunk()
    return chunks
