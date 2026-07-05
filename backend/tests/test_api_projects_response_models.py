"""API tests for the previously-untyped project browse/clear/remove/config endpoints.

Uses a fake ProjectService (rather than the real DB-backed one) so these tests
never touch the shared claude_registry.db that other project rows/state live in.
"""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import projects as projects_api
from app.main import app
from app.models.schemas import MergedConfig, ProjectConfigResponse, ProjectResponse


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class _FakeProjectService:
    pass


@pytest.mark.asyncio
async def test_browse_directory_lists_subdirectories(tmp_path, monkeypatch):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    monkeypatch.setattr(projects_api.Path, "home", classmethod(lambda cls: tmp_path))

    async with _client() as ac:
        r = await ac.get("/api/v1/projects/browse", params={"path": str(tmp_path)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["directories"] == ["alpha", "beta"]
    assert body["path"] == str(tmp_path)
    assert body["parent"] is None


@pytest.mark.asyncio
async def test_browse_directory_rejects_paths_outside_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(projects_api.Path, "home", classmethod(lambda cls: home))

    async with _client() as ac:
        r = await ac.get("/api/v1/projects/browse", params={"path": str(tmp_path)})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_remove_project_matches_message_response(monkeypatch):
    fake = _FakeProjectService()
    fake.remove_project = AsyncMock(return_value=True)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.delete("/api/v1/projects/1")
    assert r.status_code == 200, r.text
    assert r.json() == {"message": "Project removed successfully"}


@pytest.mark.asyncio
async def test_remove_project_404_when_missing(monkeypatch):
    fake = _FakeProjectService()
    fake.remove_project = AsyncMock(return_value=False)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.delete("/api/v1/projects/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_clear_active_project_matches_message_response(monkeypatch):
    fake = _FakeProjectService()
    fake.clear_active_project = AsyncMock(return_value=True)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.delete("/api/v1/projects/active")
    assert r.status_code == 200, r.text
    assert r.json() == {"message": "Active project cleared"}


@pytest.mark.asyncio
async def test_get_project_config_matches_response_model(monkeypatch):
    fake_config = {
        "project": ProjectResponse(
            id=1,
            name="demo",
            path="/tmp/demo",
            is_active=True,
            last_accessed="2026-01-01T00:00:00",
            created_at="2026-01-01T00:00:00",
        ).model_dump(),
        "config": MergedConfig(
            settings={}, mcp_servers={}, hooks={}, permissions={}, commands=[], agents=[],
        ).model_dump(),
    }
    fake = _FakeProjectService()
    fake.get_project_config = AsyncMock(return_value=fake_config)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.get("/api/v1/projects/1/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project"]["id"] == 1
    ProjectConfigResponse.model_validate(body)


@pytest.mark.asyncio
async def test_get_project_config_404_when_missing(monkeypatch):
    fake = _FakeProjectService()
    fake.get_project_config = AsyncMock(return_value=None)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.get("/api/v1/projects/999/config")
    assert r.status_code == 404
