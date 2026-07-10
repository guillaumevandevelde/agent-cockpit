"""Read-only status endpoint for the MiniMax platform: reports whether the
backend has a MiniMax API key configured, but never echoes the key itself."""
import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.runs.router as bridge_router
from app.config import settings
from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_minimax_status_reports_configured_when_key_set(monkeypatch):
    monkeypatch.setattr(settings, "minimax_api_key", "sk-test-key")
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/platforms/minimax/status")
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": True}


@pytest.mark.asyncio
async def test_minimax_status_reports_not_configured_when_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "minimax_api_key", None)
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/platforms/minimax/status")
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": False}


@pytest.mark.asyncio
async def test_minimax_status_never_echoes_the_key(monkeypatch):
    monkeypatch.setattr(settings, "minimax_api_key", "sk-super-secret")
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/platforms/minimax/status")
    assert "sk-super-secret" not in r.text


@pytest.mark.asyncio
async def test_spawn_request_passes_minimax_base_url_to_options(monkeypatch):
    captured = {}

    def fake_spawn(cli_id, options, session_name=None, host_data=None):
        captured["options"] = options
        return {"tmux_target": "s:0.0", "session_name": "s"}

    monkeypatch.setattr(bridge_router, "spawn_session", fake_spawn)

    payload = {
        "directory": "/tmp",
        "mode": "plain",
        "platform": "minimax",
        "minimax_base_url": "https://api.minimaxi.com/anthropic",
    }
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions", json=payload)

    assert r.status_code == 200, r.text
    assert captured["options"].minimax_base_url == "https://api.minimaxi.com/anthropic"
