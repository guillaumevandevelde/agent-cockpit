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
async def test_get_session_detail_rejects_path_traversal_in_project_folder():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/%2e%2e/some-session")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_session_detail_rejects_path_traversal_in_session_id():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/some-project/%2e%2e")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_sessions_rejects_path_traversal_in_project_folder():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions", params={"project_folder": "../.."})
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_list_sessions_rejects_path_traversal_even_without_projects_dir(monkeypatch, tmp_path):
    """The traversal guard must not depend on ``~/.claude/projects`` existing.

    It used to: the ``projects_dir.exists()`` early return sat *above* the
    guard, so on a host without that directory a ``../..`` was answered with
    an empty 200. Every dev box has the directory, so the test above passed
    locally and only went red on CI — which is exactly the shape of bug that
    survives for weeks. Pin the ordering here so a future refactor that moves
    the guard back down fails on any machine.
    """
    from app.services import session_service as svc

    monkeypatch.setattr(
        svc, "get_claude_projects_dir", lambda: tmp_path / "does-not-exist",
    )
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions", params={"project_folder": "../.."})
        assert r.status_code == 500, r.text
        # Sanity: the same absent directory answers a normal call with an
        # empty 200, so the 500 above really is the guard and not the
        # missing directory itself.
        ok = await ac.get("/api/v1/sessions")
    assert ok.status_code == 200, ok.text
    assert ok.json()["sessions"] == []


@pytest.mark.asyncio
async def test_pending_queue_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/sessions/pending-queue")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "size" in body
    assert isinstance(body["cards"], list)
