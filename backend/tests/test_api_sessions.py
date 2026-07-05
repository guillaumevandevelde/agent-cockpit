"""API tests for the session transcript endpoints."""
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
async def test_list_projects_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/projects")
    assert r.status_code == 200, r.text
    assert "projects" in r.json()


@pytest.mark.asyncio
async def test_list_sessions_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions")
    assert r.status_code == 200, r.text
    assert "sessions" in r.json()


@pytest.mark.asyncio
async def test_dashboard_stats_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/dashboard/stats")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_list_sessions_rejects_invalid_limit():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions", params={"limit": 0})
    assert r.status_code == 422

    async with _client() as ac:
        r = await ac.get("/api/v1/sessions", params={"limit": 1000})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_sessions_rejects_invalid_sort():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions", params={"sort_by": "bogus"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_session_detail_not_found_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/no-such-project/no-such-session")
    # Detail text is not asserted: the SPA 404 handler rewrites API 404 bodies
    # when frontend/dist is built, so only the status code is stable.
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pending_queue_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/pending-queue")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "size" in body
    assert isinstance(body["cards"], list)
