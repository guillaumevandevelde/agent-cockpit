"""API tests for the status line configuration endpoints.

Security-focused preview sandboxing has its own suite in
test_statusline_preview_security.py; these cover the HTTP contract
(happy path / 404 / 422) of the router.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_config_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/statusline")
    assert r.status_code == 200, r.text
    assert "type" in r.json()


@pytest.mark.asyncio
async def test_get_presets_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/statusline/presets")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["presets"], list)


@pytest.mark.asyncio
async def test_apply_unknown_preset_returns_404():
    async with _client() as ac:
        r = await ac.post("/api/v1/statusline/apply-preset/__nope__")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_apply_unknown_powerline_preset_returns_404():
    async with _client() as ac:
        r = await ac.post("/api/v1/statusline/apply-powerline/__nope__")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_preview_happy_path_runs_script():
    async with _client() as ac:
        r = await ac.post("/api/v1/statusline/preview", json={"script": "echo hello"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "hello" in body["output"]


@pytest.mark.asyncio
async def test_preview_requires_script_field():
    async with _client() as ac:
        r = await ac.post("/api/v1/statusline/preview", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_check_nodejs_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/statusline/check-nodejs")
    assert r.status_code == 200, r.text
    assert "available" in r.json()
