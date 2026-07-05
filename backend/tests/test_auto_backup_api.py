"""API tests for the automatic-backup settings endpoints."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app
from app.services.scheduling.scheduler import scheduler_service


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    scheduler_service.remove_auto_backup()


@pytest.mark.asyncio
async def test_get_and_update_auto_settings():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/backup/auto/settings")
        assert r.status_code == 200, r.text
        assert "retention_days" in r.json()

        r = await ac.put(
            "/api/v1/backup/auto/settings",
            json={"enabled": True, "time_of_day": "04:15", "retention_days": 10},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is True
        assert body["time_of_day"] == "04:15"
        assert body["retention_days"] == 10
        # Enabling should register the scheduler job.
        assert scheduler_service.has_auto_backup() is True

        # Disabling should remove the job.
        r = await ac.put("/api/v1/backup/auto/settings", json={"enabled": False})
        assert r.status_code == 200
        assert scheduler_service.has_auto_backup() is False


@pytest.mark.asyncio
async def test_update_rejects_invalid_time():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.put(
            "/api/v1/backup/auto/settings", json={"time_of_day": "99:99"}
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_run_now_rejected_when_disabled():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.put("/api/v1/backup/auto/settings", json={"enabled": False})
        r = await ac.post("/api/v1/backup/auto/run")
        assert r.status_code == 400
