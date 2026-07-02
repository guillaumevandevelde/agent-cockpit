"""API tests for the agent-activity endpoints (live agent discovery)."""
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_live_agents_happy_path():
    fake_sessions = [
        {
            "tmux_target": "sess:0.0",
            "session_name": "sess",
            "cwd": "/home/u/proj",
            "pid": "1234",
            "provider": "claude-code",
        }
    ]
    with patch("app.api.v1.agent_activity.discover_agent_sessions", return_value=fake_sessions), \
         patch("app.api.v1.agent_activity.capture_pane_preview", return_value="line1\nline2\n"):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/live")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    agent = body["agents"][0]
    assert agent["session_name"] == "sess"
    assert agent["provider"] == "claude-code"
    assert agent["preview"] == "line1\nline2"
    assert agent["status"] == "active"


@pytest.mark.asyncio
async def test_live_agents_empty_when_no_sessions():
    with patch("app.api.v1.agent_activity.discover_agent_sessions", return_value=[]):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/live")
    assert r.status_code == 200, r.text
    assert r.json() == {"agents": [], "count": 0}


@pytest.mark.asyncio
async def test_live_agents_status_waiting_inferred_from_preview():
    fake_sessions = [{"tmux_target": "s:0.0", "provider": "claude-code"}]
    with patch("app.api.v1.agent_activity.discover_agent_sessions", return_value=fake_sessions), \
         patch("app.api.v1.agent_activity.capture_pane_preview", return_value="Waiting for permission to run"):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/live")
    assert r.status_code == 200, r.text
    assert r.json()["agents"][0]["status"] == "waiting"


@pytest.mark.asyncio
async def test_live_agents_rejects_preview_lines_out_of_range():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-activity/live", params={"preview_lines": 0})
    assert r.status_code == 422

    async with _client() as ac:
        r = await ac.get("/api/v1/agent-activity/live", params={"preview_lines": 99})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_activity_summary_groups_by_provider():
    fake_sessions = [
        {"provider": "claude-code"},
        {"provider": "claude-code"},
        {"provider": "codex"},
    ]
    with patch("app.api.v1.agent_activity.discover_agent_sessions", return_value=fake_sessions):
        async with _client() as ac:
            r = await ac.get("/api/v1/agent-activity/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["by_provider"] == {"claude-code": 2, "codex": 1}
    assert body["has_active"] is True
