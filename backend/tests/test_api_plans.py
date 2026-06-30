"""API tests for the plan history browser endpoints."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


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
    with patch("app.api.v1.plans.PlanService.resolve_plans_dir", return_value="/tmp/no-plans"), \
         patch("app.api.v1.plans.PlanService.get_plan", return_value=None):
        async with _client() as ac:
            r = await ac.get("/api/v1/plans/does-not-exist.md")
    # Detail text is not asserted: the SPA 404 handler rewrites API 404 bodies
    # when frontend/dist is built, so only the status code is stable.
    assert r.status_code == 404
