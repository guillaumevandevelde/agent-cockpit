import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import init_db
from app.main import app


@pytest.mark.asyncio
async def test_api_token_protects_api_routes(monkeypatch):
    # ASGITransport does not run the app lifespan, so create the main-app schema
    # explicitly: on a fresh DB (CI) the tables the status route touches are
    # otherwise missing — the test then only passes when a populated
    # claude_registry.db happens to exist. create_all is idempotent / non-destructive.
    await init_db()
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
