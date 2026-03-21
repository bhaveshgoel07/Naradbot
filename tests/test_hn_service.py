from __future__ import annotations

from types import SimpleNamespace

import pytest

from personal_agent.graph.state import HNWorkflowRequest
from personal_agent.hn.service import HNService


class DictReturningWorkflow:
    async def run(self, request: HNWorkflowRequest) -> dict[str, object]:
        return {
            "request": {
                "trigger_source": request.trigger_source,
                "requested_by": request.requested_by,
                "publish_to_discord": request.publish_to_discord,
            },
            "stories": [SimpleNamespace(id=1), SimpleNamespace(id=2)],
            "details": {"bucket_sizes": {"summary": 2}},
            "published_messages": {"interesting": "msg-2", "summary": "msg-1"},
            "started_at": "2026-03-20T00:00:00+00:00",
            "finished_at": "2026-03-20T00:01:00+00:00",
        }


@pytest.mark.asyncio
async def test_hn_service_supports_dict_state_from_workflow() -> None:
    service = HNService(workflow=DictReturningWorkflow())

    result = await service.run(
        trigger_source="discord_command",
        requested_by="tester#1234",
        publish_to_discord=True,
    )

    assert result == {
        "trigger_source": "discord_command",
        "requested_by": "tester#1234",
        "story_count": 2,
        "bucket_sizes": {"summary": 2},
        "published_channels": ["interesting", "summary"],
        "started_at": "2026-03-20T00:00:00+00:00",
        "finished_at": "2026-03-20T00:01:00+00:00",
        "details": {"bucket_sizes": {"summary": 2}},
    }
