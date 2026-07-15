"""API tests for the plan history browser endpoints.

Kanban-DB-backed (kanban card 727470a8). The legacy file-backed PlanService
is no longer used by /api/v1/plans; these tests verify the API against the
real kanban-DB CRUD via ``KanbanPlanService``.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_list_plans_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/plans")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "plans" in body
    assert "total" in body


@pytest.mark.asyncio
async def test_plan_stats_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/stats")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_search_requires_query():
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/search")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_search_rejects_empty_query():
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/search", params={"q": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_plan_not_found_returns_404():
    """Empty kanban plans table → /plans/{filename} returns 404."""
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/does-not-exist.md")
    # Detail text is not asserted: the SPA 404 handler rewrites API 404 bodies
    # when frontend/dist is built, so only the status code is stable.
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_rejects_path_traversal():
    """Slug with ``..`` must be rejected with 400, not silently coerced."""
    # ``..md`` survives URL decoding (no `/` to be re-segmented into a
    # different route), so the FastAPI router reaches the handler and our
    # slug validator can reject it.
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/..md")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_create_plan_persists_with_project_key_fk():
    """POST /plans writes a kanban_plans row with the resolved project_key."""
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/plans",
            json={"filename": "build-widget.md",
                  "content": "# Plan: Build widget\n\nThe plan."},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["plan"]["slug"] == "build-widget"
    assert body["plan"]["filename"] == "build-widget.md"
    assert body["plan"]["title"] == "Build widget"
    assert "project_key" in body["plan"]
    assert body["plan"]["project_key"].startswith("slug:")


@pytest.mark.asyncio
async def test_create_then_get_round_trip():
    async with _client() as ac:
        create = await ac.post(
            "/api/v1/plans",
            json={"filename": "rt.md",
                  "content": "# Plan: RT\n\nBody for round-trip."},
        )
        assert create.status_code == 201, create.text
        slug = create.json()["plan"]["slug"]

        get = await ac.get(f"/api/v1/plans/{slug}.md")
    assert get.status_code == 200
    body = get.json()["plan"]
    assert body["slug"] == "rt"
    assert body["content"] == "# Plan: RT\n\nBody for round-trip."


@pytest.mark.asyncio
async def test_delete_plan_returns_204():
    async with _client() as ac:
        await ac.post(
            "/api/v1/plans",
            json={"filename": "del.md", "content": "bye"},
        )
        r = await ac.delete("/api/v1/plans/del.md")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_plan_missing_returns_404():
    async with _client() as ac:
        r = await ac.delete("/api/v1/plans/never-was.md")
    assert r.status_code == 404
