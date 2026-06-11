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
async def test_hook_event_updates_idle_state():
    from app.services.scheduling.idle_state import idle_state
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "Stop", "session_id": "s1", "cwd": "/tmp/idletest"})
        assert r.status_code == 200
    assert idle_state.is_idle("/tmp/idletest") is True
