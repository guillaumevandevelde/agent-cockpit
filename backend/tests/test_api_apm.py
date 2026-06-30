"""API tests for the APM (Agent Package Manager) endpoints."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_status_happy_path():
    with patch("app.api.v1.apm.ApmService.get_status", return_value={"installed": False}):
        async with _client() as ac:
            r = await ac.get("/api/v1/apm/status")
    assert r.status_code == 200, r.text
    assert r.json() == {"installed": False}


@pytest.mark.asyncio
async def test_list_dependencies_happy_path():
    with patch("app.api.v1.apm.ApmService.list_dependencies", return_value={"dependencies": []}):
        async with _client() as ac:
            r = await ac.get("/api/v1/apm/deps")
    assert r.status_code == 200, r.text
    assert r.json() == {"dependencies": []}


@pytest.mark.asyncio
async def test_add_dependency_failure_returns_400():
    with patch("app.api.v1.apm.ApmService.add_dependency",
               return_value={"success": False, "message": "bad source"}):
        async with _client() as ac:
            r = await ac.post("/api/v1/apm/deps", json={"name": "pkg", "source": "owner/repo"})
    assert r.status_code == 400
    assert r.json()["detail"] == "bad source"


@pytest.mark.asyncio
async def test_remove_dependency_not_found_returns_404():
    with patch("app.api.v1.apm.ApmService.remove_dependency",
               return_value={"success": False, "message": "not installed"}):
        async with _client() as ac:
            r = await ac.delete("/api/v1/apm/deps/ghost")
    assert r.status_code == 404
    assert r.json()["detail"] == "not installed"


@pytest.mark.asyncio
async def test_add_dependency_validation_error():
    async with _client() as ac:
        r = await ac.post("/api/v1/apm/deps", json={"name": "", "source": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_sync_requires_both_projects():
    async with _client() as ac:
        r = await ac.post("/api/v1/apm/sync", json={"source_project": "/a"})
    assert r.status_code == 422
