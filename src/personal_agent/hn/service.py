from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from personal_agent.graph.main import HNWorkflow
from personal_agent.graph.state import HNWorkflowRequest


def _lookup(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


@dataclass(slots=True)
class HNService:
    """Application service for running the Hacker News LangGraph workflow."""

    workflow: HNWorkflow

    async def run(self, *, trigger_source: str, requested_by: str | None, publish_to_discord: bool) -> dict[str, Any]:
        state = await self.workflow.run(
            HNWorkflowRequest(
                trigger_source=trigger_source,
                requested_by=requested_by,
                publish_to_discord=publish_to_discord,
            )
        )
        request = _lookup(state, "request", {})
        stories = _lookup(state, "stories", [])
        details = _lookup(state, "details", {})
        published_messages = _lookup(state, "published_messages", {})
        started_at = _lookup(state, "started_at")
        finished_at = _lookup(state, "finished_at")
        return {
            "trigger_source": _lookup(request, "trigger_source"),
            "requested_by": _lookup(request, "requested_by"),
            "story_count": len(stories),
            "bucket_sizes": _lookup(details, "bucket_sizes", {}),
            "published_channels": sorted(published_messages),
            "started_at": started_at,
            "finished_at": finished_at,
            "details": details,
        }
