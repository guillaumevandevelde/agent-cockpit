"""API tests for the memory (CLAUDE.md / rules) endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_hierarchy_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/memory/hierarchy")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["files"], list)


@pytest.mark.asyncio
async def test_get_file_missing_reports_not_exists(tmp_path):
    missing = str(tmp_path / "nope" / "CLAUDE.md")
    async with _client() as ac:
        r = await ac.get("/api/v1/memory/file", params={"file_path": missing})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] is False
    assert body["path"] == missing


@pytest.mark.asyncio
async def test_get_file_requires_path_query():
    async with _client() as ac:
        r = await ac.get("/api/v1/memory/file")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_save_and_read_roundtrip(tmp_path):
    target = str(tmp_path / "CLAUDE.md")
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/memory/file",
            params={"file_path": target},
            json={"content": "# hello"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

        r = await ac.get("/api/v1/memory/file", params={"file_path": target})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "# hello"


@pytest.mark.asyncio
async def test_auto_memory_requires_project_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/memory/auto-memory")
    assert r.status_code == 422
