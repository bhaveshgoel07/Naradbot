from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ExecutionProvider(Protocol):
    """Abstract interface for future sandboxed command execution."""

    async def run_code(self, language: str, code: str) -> str:
        raise NotImplementedError


@dataclass(slots=True)
class BlaxelExecutionProvider:
    """Placeholder for future Blaxel-based remote execution integration."""

    endpoint: str | None = None

    async def run_code(self, language: str, code: str) -> str:
        return (
            "Blaxel execution is not wired yet. "
            f"Received language={language!r} with {len(code)} characters of code."
        )
