"""API tests for the plugin management endpoints."""
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.schemas import PluginListResponse


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_plugins_happy_path():
    fake = PluginListResponse(plugins=[])
    service = MagicMock()
    service.list_installed_plugins.return_value = fake
    with patch("app.api.v1.plugins.PluginService", return_value=service):
        async with _client() as ac:
            r = await ac.get("/api/v1/plugins")
    assert r.status_code == 200, r.text
    assert r.json() == {"plugins": []}
    service.list_installed_plugins.assert_called_once()


@pytest.mark.asyncio
async def test_get_plugin_not_found_returns_404():
    service = MagicMock()
    service.get_plugin_details.return_value = None
    with patch("app.api.v1.plugins.PluginService", return_value=service):
        async with _client() as ac:
            r = await ac.get("/api/v1/plugins/does-not-exist")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_failure_surfaces_500():
    service = MagicMock()
    service.list_installed_plugins.side_effect = RuntimeError("boom")
    with patch("app.api.v1.plugins.PluginService", return_value=service):
        async with _client() as ac:
            r = await ac.get("/api/v1/plugins")
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]


@pytest.mark.asyncio
async def test_validate_plugin_requires_body():
    async with _client() as ac:
        r = await ac.post("/api/v1/plugins/validate", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_marketplace_rejects_empty_input():
    async with _client() as ac:
        r = await ac.post("/api/v1/plugins/marketplaces", json={"input": ""})
    assert r.status_code == 400
    assert "required" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_marketplace_plugin_details_not_found_returns_404():
    service = MagicMock()
    service.get_marketplace_plugin_details.return_value = None
    with patch("app.api.v1.plugins.PluginService", return_value=service):
        async with _client() as ac:
            r = await ac.get("/api/v1/plugins/marketplace/mp/plugin/nope")
    assert r.status_code == 404
