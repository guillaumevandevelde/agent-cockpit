import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# Schema + per-test reset handled by ``_reset_app_database_tables`` +
# ``_patch_app_database`` in conftest.py. No per-file monkeypatch of
# ``app.database.AsyncSessionLocal`` needed — every consumer (including
# ``get_db()`` resolved at request time, routers that did
# ``from app.database import AsyncSessionLocal`` at import time, and the
# inner ``agent_mail`` API calls) is rebound to the test factory by the
# session-scoped identity-swap.


@pytest.mark.asyncio
async def test_register_two_members_then_list_team(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "provider": "claude-code",
            "cwd": str(tmp_path / "a"), "session_key": "cc:a",
        })
        assert r1.status_code == 200
        member_a = r1.json()["member"]["id"]

        r2 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "provider": "claude-code",
            "cwd": str(tmp_path / "b"), "session_key": "cc:b",
        })
        member_b = r2.json()["member"]["id"]

        r3 = await client.get("/api/v1/agent-mail/team")
        member_ids = {m["id"] for m in r3.json()["members"]}
        assert {member_a, member_b}.issubset(member_ids)


@pytest.mark.asyncio
async def test_update_member_role_and_charter(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:x",
        })
        member_id = r1.json()["member"]["id"]
        r2 = await client.patch(f"/api/v1/agent-mail/members/{member_id}", json={
            "role": "reviewer", "charter": "reviews PRs",
        })
        assert r2.status_code == 200
        assert r2.json()["role"] == "reviewer"


