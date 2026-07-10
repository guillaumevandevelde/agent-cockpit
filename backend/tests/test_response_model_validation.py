"""Explicit Pydantic v2 model_validate() checks for the response models added to
agent_activity.py and codex_config.py, complementing the existing HTTP smoke
tests in test_api_agent_activity.py / test_api_codex_config.py."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import agent_activity as agent_activity_api
from app.api.v1 import codex_config as codex_config_api
from app.main import app
from app.services.codex_config_service import CodexConfigService


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_live_agents_response_validates():
    fake_sessions = [
        {
            "tmux_target": "sess:0.0",
            "session_name": "sess",
            "cwd": "/home/u/proj",
            "pid": "1234",
            "provider": "claude-code",
        }
    ]
    with patch("app.api.v1.run_activity.discover_agent_sessions", return_value=fake_sessions), \
         patch("app.api.v1.run_activity.capture_pane_preview", return_value="line1\n"):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/live")
    assert r.status_code == 200, r.text
    agent_activity_api.RunActivityListResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_activity_summary_response_validates():
    with patch("app.api.v1.run_activity.discover_agent_sessions", return_value=[]):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/summary")
    assert r.status_code == 200, r.text
    agent_activity_api.ActivitySummaryResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_codex_config_response_validates(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('model = "gpt-5.1-codex"\n', encoding="utf-8")
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.get("/api/v1/codex-config")
    assert r.status_code == 200, r.text
    codex_config_api.CodexConfigResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_codex_config_files_response_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.get("/api/v1/codex-config/files")
    assert r.status_code == 200, r.text
    codex_config_api.CodexConfigFileListResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_codex_config_update_response_validates(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('model = "old"\n', encoding="utf-8")
    monkeypatch.setattr(
        "app.api.v1.codex_config.CodexConfigService",
        lambda: CodexConfigService(codex_home=tmp_path),
    )
    async with _client() as ac:
        r = await ac.patch("/api/v1/codex-config", json={"settings": {"model": "new"}, "features": {}})
    assert r.status_code == 200, r.text
    codex_config_api.CodexConfigUpdateResponse.model_validate(r.json())
