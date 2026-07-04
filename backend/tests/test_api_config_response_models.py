"""API tests for the previously-untyped config settings/resolved/scopes endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import config as config_api
from app.main import app
from app.models.schemas import (
    AllScopedSettingsResponse,
    ResolvedConfigResponse,
    ScopedSettingsResponse,
)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_settings_by_scope_matches_response_model(monkeypatch):
    monkeypatch.setattr(
        config_api.config_service,
        "get_settings_by_scope",
        lambda scope, project_path=None: {"permissions": {"allow": ["Bash(ls:*)"]}},
    )

    async with _client() as ac:
        r = await ac.get("/api/v1/config/settings/user")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user"
    ScopedSettingsResponse.model_validate(body)


@pytest.mark.asyncio
async def test_get_all_scoped_settings_matches_response_model(monkeypatch):
    fake_scopes = {"managed": {}, "user": {"foo": "bar"}, "project": {}, "local": {}}
    monkeypatch.setattr(
        config_api.config_service,
        "get_all_scoped_settings",
        lambda project_path=None: fake_scopes,
    )

    async with _client() as ac:
        r = await ac.get("/api/v1/config/scopes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scopes"] == fake_scopes
    AllScopedSettingsResponse.model_validate(body)


@pytest.mark.asyncio
async def test_get_resolved_config_matches_response_model(monkeypatch):
    fake_resolved = {
        "resolved": {
            "permissions.allow": {
                "effective_value": ["Bash(ls:*)"],
                "source_scope": "user",
                "values_by_scope": {"user": ["Bash(ls:*)"]},
            }
        },
        "scopes": {
            "managed": {"settings": {}, "path": "/x/managed.json", "exists": False, "readonly": True},
            "user": {"settings": {}, "path": "/x/user.json", "exists": True, "readonly": False},
            "project": {"settings": {}, "path": None, "exists": False, "readonly": False},
            "local": {"settings": {}, "path": None, "exists": False, "readonly": False},
        },
    }
    monkeypatch.setattr(
        config_api.config_service,
        "get_resolved_config",
        lambda project_path=None: fake_resolved,
    )

    async with _client() as ac:
        r = await ac.get("/api/v1/config/resolved")
    assert r.status_code == 200, r.text
    ResolvedConfigResponse.model_validate(r.json())
