"""Smoke tests for the (previously untested) permissions async endpoints."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.schemas import PermissionListResponse, PermissionRule, PermissionSettings


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _fake_response() -> PermissionListResponse:
    return PermissionListResponse(
        rules=[
            PermissionRule(id="1", type="allow", pattern="Bash(ls)", scope="user"),
            PermissionRule(id="2", type="deny", pattern="Bash(rm)", scope="project"),
        ],
        settings=PermissionSettings(),
    )


@pytest.mark.asyncio
async def test_list_permissions_returns_all_rules():
    with patch("app.api.v1.permissions.PermissionService.list_permissions", return_value=_fake_response()):
        async with _client() as ac:
            r = await ac.get("/api/v1/permissions")
    assert r.status_code == 200, r.text
    assert len(r.json()["rules"]) == 2


@pytest.mark.asyncio
async def test_list_permissions_by_scope_filters_rules():
    with patch("app.api.v1.permissions.PermissionService.list_permissions", return_value=_fake_response()):
        async with _client() as ac:
            r = await ac.get("/api/v1/permissions/scope/project")
    assert r.status_code == 200, r.text
    rules = r.json()["rules"]
    assert len(rules) == 1
    assert rules[0]["scope"] == "project"


@pytest.mark.asyncio
async def test_list_permissions_by_scope_rejects_invalid_scope():
    async with _client() as ac:
        r = await ac.get("/api/v1/permissions/scope/bogus")
    assert r.status_code == 400
