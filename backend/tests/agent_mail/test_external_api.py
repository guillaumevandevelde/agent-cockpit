import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# Schema + per-test reset handled by conftest fixtures.


@pytest.mark.asyncio
async def test_create_actor_requires_loopback():
    transport = ASGITransport(app=app, client=("1.2.3.4", 12345))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "remote", "display_name": "Remote",
        })
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_full_actor_lifecycle_and_context_request(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:1",
        })
        member_id = r1.json()["member"]["id"]

        r2 = await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "openclaw", "display_name": "OpenClaw",
        })
        assert r2.status_code == 200
        token = r2.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        r3 = await client.get("/api/v1/external/agent-mail/actors/me", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["actor_key"] == "openclaw"

        r4 = await client.post("/api/v1/external/agent-mail/context-requests", headers=headers, json={
            "recipient_member_id": member_id, "body_markdown": "need context", "why_needed": "testing",
        })
        assert r4.status_code == 200
        message_id = r4.json()["message"]["id"]

        r5 = await client.get(f"/api/v1/external/agent-mail/requests/{message_id}/status", headers=headers)
        assert r5.status_code == 200
        assert r5.json()["request_status"] == "pending"


@pytest.mark.asyncio
async def test_cross_actor_thread_access_forbidden(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:1",
        })
        member_id = r1.json()["member"]["id"]

        actor1 = (await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "actor1", "display_name": "A1",
        })).json()
        actor2 = (await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "actor2", "display_name": "A2",
        })).json()

        r2 = await client.post(
            "/api/v1/external/agent-mail/messages",
            headers={"Authorization": f"Bearer {actor1['token']}"},
            json={"recipient_member_id": member_id, "body_markdown": "hi"},
        )
        message_id = r2.json()["message"]["id"]

        r3 = await client.get(
            f"/api/v1/external/agent-mail/threads/{message_id}",
            headers={"Authorization": f"Bearer {actor2['token']}"},
        )
        assert r3.status_code == 403
