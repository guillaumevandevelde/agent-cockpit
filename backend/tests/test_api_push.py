"""API tests for the Web Push endpoints (isolated in-memory DB, no network)."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.services import push_service as svc


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    # Never hit the network in API tests.
    monkeypatch.setattr(svc, "_webpush_sync", lambda sub, payload: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


def _sub_body(endpoint="https://push.example/1", **kw):
    body = {
        "endpoint": endpoint,
        "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
    }
    body.update(kw)
    return body


@pytest.mark.asyncio
async def test_vapid_public_key(client):
    r = await client.get("/api/v1/push/vapid-public-key")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["public_key"]


@pytest.mark.asyncio
async def test_subscribe_then_update_and_unsubscribe(client):
    r = await client.post("/api/v1/push/subscribe", json=_sub_body())
    assert r.status_code == 201, r.text
    assert r.json()["mute_input"] is False

    r = await client.patch(
        "/api/v1/push/preferences",
        json={"endpoint": "https://push.example/1", "mute_error": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mute_error"] is True

    r = await client.post(
        "/api/v1/push/unsubscribe", json={"endpoint": "https://push.example/1"}
    )
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_preferences_missing_returns_404(client):
    r = await client.patch(
        "/api/v1/push/preferences", json={"endpoint": "https://nope"}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_subscribe_is_idempotent(client):
    await client.post("/api/v1/push/subscribe", json=_sub_body())
    await client.post("/api/v1/push/subscribe", json=_sub_body(mute_input=True))
    r = await client.post("/api/v1/push/test")
    assert r.status_code == 200
    # One subscription reached despite two subscribe calls for the same endpoint.
    assert r.json()["sent"] == 1


@pytest.mark.asyncio
async def test_test_endpoint_reports_zero_without_subscriptions(client):
    r = await client.post("/api/v1/push/test")
    assert r.status_code == 200
    assert r.json()["sent"] == 0
