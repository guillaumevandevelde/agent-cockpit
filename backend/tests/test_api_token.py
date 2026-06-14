import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_api_token_protects_api_routes(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/api/v1/status")
        authorized = await client.get(
            "/api/v1/status",
            headers={"Authorization": "Bearer test-secret"},
        )
        health = await client.get("/api/v1/health")

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert health.status_code == 200
