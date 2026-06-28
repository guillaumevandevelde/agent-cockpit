"""Tests for presence WebSocket one-time token authentication."""
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.database import init_db
from app.main import app


@pytest.mark.asyncio
async def test_presence_token_endpoint_returns_token():
    """GET /presence/token returns a one-time token."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/presence/token")
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert len(data["token"]) > 10


@pytest.mark.asyncio
async def test_presence_token_endpoint_requires_auth_when_api_token_configured(monkeypatch):
    """GET /presence/token requires Authorization header when api_token is set."""
    await init_db()
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/presence/token")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_presence_token_endpoint_accessible_with_valid_auth(monkeypatch):
    """GET /presence/token is accessible with a valid Authorization header."""
    await init_db()
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(
            "/api/v1/presence/token",
            headers={"Authorization": "Bearer test-secret"},
        )
    assert r.status_code == 200
    assert "token" in r.json()


def test_presence_ws_rejects_invalid_token_when_api_token_configured(monkeypatch):
    """WebSocket closes with 4401 when an invalid one-time token is supplied."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/presence/ws?token=bad-token") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4401


def test_presence_ws_connects_without_token_when_no_api_token_configured(monkeypatch):
    """WebSocket connects without any token when no api_token is configured."""
    monkeypatch.setattr(settings, "api_token", "")
    client = TestClient(app)
    with client.websocket_connect("/api/v1/presence/ws") as ws:
        pass  # Connection accepted — no exception raised


def test_presence_ws_rejects_old_api_token_query_param(monkeypatch):
    """Old ?api_token= query param no longer authenticates the WebSocket."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/presence/ws?api_token=test-secret") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4401


def test_presence_ws_accepts_valid_one_time_token(monkeypatch):
    """WebSocket connects successfully with a valid one-time token from /token."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    client = TestClient(app)
    resp = client.get(
        "/api/v1/presence/token",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    with client.websocket_connect(f"/api/v1/presence/ws?token={token}") as ws:
        pass  # Connection accepted


def test_presence_ws_one_time_token_cannot_be_reused(monkeypatch):
    """A one-time token is consumed on the first WebSocket connection."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    client = TestClient(app)
    resp = client.get(
        "/api/v1/presence/token",
        headers={"Authorization": "Bearer test-secret"},
    )
    token = resp.json()["token"]
    # First connection — succeeds
    with client.websocket_connect(f"/api/v1/presence/ws?token={token}") as ws:
        pass
    # Second connection with same token — rejected
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/presence/ws?token={token}") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4401
