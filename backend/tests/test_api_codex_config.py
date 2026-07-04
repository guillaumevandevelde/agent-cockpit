"""Smoke tests for the (previously untested) codex-config async endpoints."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.codex_config_service import CodexConfigService


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_codex_config_returns_summary(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('model = "gpt-5.1-codex"\n', encoding="utf-8")
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.get("/api/v1/codex-config")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["model"] == "gpt-5.1-codex"


@pytest.mark.asyncio
async def test_get_codex_config_file_rejects_path_outside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.get("/api/v1/codex-config/file", params={"path": "/etc/passwd"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_codex_config_persists_and_replace_delegates(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('model = "old"\n', encoding="utf-8")
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.patch("/api/v1/codex-config", json={"settings": {"model": "new"}, "features": {}})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["summary"]["model"] == "new"

    async with _client() as ac:
        r2 = await ac.put("/api/v1/codex-config", json={"settings": {"model": "newer"}, "features": {}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["config"]["summary"]["model"] == "newer"
