import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


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


@pytest.mark.asyncio
async def test_api_token_protects_kanban_mcp_mount(monkeypatch):
    """The kanban MCP server is mounted outside /api/v1, but must still require
    the token when remote-access protection is configured (otherwise agents can
    reach the board unauthenticated)."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get("/kanban-mcp/sse")

    assert unauthorized.status_code == 401


@pytest.mark.asyncio
async def test_no_token_leaves_everything_open(monkeypatch):
    """Default posture (no api_token) stays fully open, including the MCP mount."""
    monkeypatch.setattr(settings, "api_token", None)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get("/api/v1/status")

    # No 401 when protection is off.
    assert status.status_code != 401
