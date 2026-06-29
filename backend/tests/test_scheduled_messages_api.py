import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import Base, engine


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_create_list_delete_once():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "hi",
                   "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]

        r = await ac.get("/api/v1/scheduled-messages")
        assert any(m["id"] == mid for m in r.json()["items"])

        r = await ac.delete(f"/api/v1/scheduled-messages/{mid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_create_cron_and_attempts_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "daily",
                   "trigger_type": "cron", "cron_expr": "0 9 * * 1-5"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = await ac.get(f"/api/v1/scheduled-messages/{mid}/attempts")
        assert r.status_code == 200
        assert r.json() == []
        await ac.delete(f"/api/v1/scheduled-messages/{mid}")


@pytest.mark.asyncio
async def test_delete_history_removes_terminal_messages():
    from sqlalchemy import update
    from app.database import AsyncSessionLocal
    from app.models.scheduled_message import ScheduledMessage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        once_payload = {"target_project": "/tmp", "message": "x",
                        "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r1 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        r2 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        r3 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        id_delivered = r1.json()["id"]
        id_failed = r2.json()["id"]
        id_scheduled = r3.json()["id"]

    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ScheduledMessage)
            .where(ScheduledMessage.id == id_delivered)
            .values(status="delivered")
        )
        await s.execute(
            update(ScheduledMessage)
            .where(ScheduledMessage.id == id_failed)
            .values(status="failed")
        )
        await s.commit()

    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/scheduled-messages/history")
        assert r.status_code == 200
        assert r.json()["deleted"] >= 2

        remaining = (await ac.get("/api/v1/scheduled-messages")).json()["items"]
        remaining_ids = [m["id"] for m in remaining]
        assert id_scheduled in remaining_ids
        assert id_delivered not in remaining_ids
        assert id_failed not in remaining_ids

        await ac.delete(f"/api/v1/scheduled-messages/{id_scheduled}")


@pytest.mark.asyncio
async def test_delete_history_when_nothing_to_clean():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/scheduled-messages/history")
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


@pytest.mark.asyncio
async def test_hook_event_updates_idle_state():
    from app.services.scheduling.idle_state import idle_state
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "Stop", "session_id": "s1", "cwd": "/tmp/idletest"})
        assert r.status_code == 200
    assert idle_state.is_idle("/tmp/idletest") is True


@pytest.mark.asyncio
async def test_hook_event_populates_session_registry():
    from app.services.scheduling.session_registry import session_registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "SessionStart", "session_id": "sX",
                                "cwd": "/proj", "tmux_pane": "%7"})
        assert r.status_code == 200
    assert session_registry.pane_for("sX") == "%7"
