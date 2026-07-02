import pytest

from app.database import Base, engine, AsyncSessionLocal
from app.services.presence_service import PresenceService


@pytest.mark.asyncio
async def test_process_event_stores_and_exposes_tmux_pane():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {
                "session_id": "sess-pane-1",
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/home/guillaume/dev/x",
                "tmux_pane": "%7",
            },
            db,
        )
        await db.commit()
    assert resp.tmux_pane == "%7"


@pytest.mark.asyncio
async def test_absent_tmux_pane_does_not_overwrite_existing():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {
                "session_id": "sess-pane-2",
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/home/guillaume/dev/y",
                "tmux_pane": "%3",
            },
            db,
        )
        # A later event without tmux_pane must not clear the stored value.
        resp = await service.process_event(
            {
                "session_id": "sess-pane-2",
                "hook_event_name": "Stop",
                "cwd": "/home/guillaume/dev/y",
            },
            db,
        )
        await db.commit()
    assert resp.tmux_pane == "%3"


@pytest.mark.asyncio
async def test_config_snippet_is_command_hook_with_tmux_pane():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/presence/config-snippet")
    assert r.status_code == 200
    snippet = r.json()["snippet"]
    stop_hook = snippet["hooks"]["Stop"][0]["hooks"][0]
    assert stop_hook["type"] == "command"
    assert "$TMUX_PANE" in stop_hook["command"]
    assert "tmux_pane" in stop_hook["command"]
    assert "/api/v1/presence/events" in stop_hook["command"]
    # Normalises Claude Code's field names to what PresenceEventIn expects.
    assert ".tool_response" in stop_hook["command"]
    assert ".prompt" in stop_hook["command"]


@pytest.mark.asyncio
async def test_config_snippet_derives_events_url_from_request():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://example.test") as ac:
        r = await ac.get("/api/v1/presence/config-snippet")
    cmd = r.json()["snippet"]["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "http://example.test/api/v1/presence/events" in cmd
    assert "localhost:8000" not in cmd


@pytest.mark.asyncio
async def test_config_snippet_honours_public_base_url(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.config import settings

    monkeypatch.setattr(settings, "public_base_url", "https://cockpit.example.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/presence/config-snippet")
    cmd = r.json()["snippet"]["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "https://cockpit.example.com/api/v1/presence/events" in cmd


@pytest.mark.asyncio
async def test_failed_tool_result_marks_session_error():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {
                "session_id": "sess-err-1",
                "hook_event_name": "PostToolUse",
                "cwd": "/home/guillaume/dev/z",
                "tool_name": "Bash",
                "tool_input": {"command": "false"},
                # After the command hook's normalisation this is the CC
                # `tool_response` surfaced as `tool_result`.
                "tool_result": {"exit_code": 1},
            },
            db,
        )
        await db.commit()
    assert resp.status == "error"
