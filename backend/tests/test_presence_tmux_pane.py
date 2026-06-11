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
