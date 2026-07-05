"""API tests for the system status / resources endpoints."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_system_status_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "active_sessions" in body
    assert "providers" in body


@pytest.mark.asyncio
async def test_system_resources_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/system-resources")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["memory_status"] in ("comfortable", "warning", "critical")
    assert body["max_active_sessions"] >= 0
    assert body["memory_total_gb"] > 0
