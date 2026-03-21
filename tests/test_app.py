from __future__ import annotations

import httpx
import pytest

from personal_agent.app import create_app
from personal_agent.config.settings import Settings


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    app = create_app(
        Settings(
            environment="test",
            sqlite_path="data/test_app.db",
            discord_bot_token=None,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
