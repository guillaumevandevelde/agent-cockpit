"""API tests for the output-style management endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_output_styles_happy_path():
    async with _client() as ac:
        r = await ac.get("/api/v1/output-styles")
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["output_styles"], list)


@pytest.mark.asyncio
async def test_get_output_style_invalid_scope_returns_400():
    async with _client() as ac:
        r = await ac.get("/api/v1/output-styles/bogus/some-name")
    assert r.status_code == 400
    assert "scope" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_output_style_not_found_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/output-styles/user/__definitely_missing__")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_output_style_invalid_scope_returns_400():
    payload = {"name": "x", "scope": "bogus", "description": "d", "content": "c"}
    async with _client() as ac:
        r = await ac.post("/api/v1/output-styles", json=payload)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_output_style_validation_error():
    async with _client() as ac:
        r = await ac.post("/api/v1/output-styles", json={})
    assert r.status_code == 422
