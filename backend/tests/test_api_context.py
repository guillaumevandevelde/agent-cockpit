"""API tests for the context-window analysis endpoints."""
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
async def test_active_sessions_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/context/active")
    assert r.status_code == 200, r.text
    assert "sessions" in r.json()


@pytest.mark.asyncio
async def test_session_context_not_found_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/context/no-such-project/no-such-session")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
