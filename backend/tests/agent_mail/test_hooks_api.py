import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
# Schema + per-test reset handled by conftest fixtures. The
# ``_patch_app_database`` fixture now patches ``AsyncSessionLocal`` on
# ``app.database`` itself (and every module that imported it) so even
# ``get_db()`` — which reads the module attribute at request time — sees
# the test factory through the real ASGI app.


@pytest.mark.asyncio
async def test_session_start_hook_registers_and_returns_context(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "abc123", "cwd": str(tmp_path), "provider": "claude-code",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "Agent Mail" in body["hookSpecificOutput"]["additionalContext"]


@pytest.mark.asyncio
async def test_session_start_hook_missing_cwd_returns_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/hooks/session-start", json={"session_id": "x"})
        assert r.status_code == 200
        assert r.json() == {}


@pytest.mark.asyncio
async def test_session_end_hook_marks_offline(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "end-me", "cwd": str(tmp_path),
        })
        r = await client.post("/api/v1/agent-mail/hooks/session-end", json={"session_id": "end-me"})
        assert r.status_code == 200

        team = (await client.get("/api/v1/agent-mail/team")).json()["members"]
        member = next(m for m in team if any(s["session_key"] == "cc:end-me" for s in m["sessions"]))
        session = next(s for s in member["sessions"] if s["session_key"] == "cc:end-me")
        assert session["mailbox_status"] == "offline"


@pytest.mark.asyncio
async def test_post_tool_use_hook_records_activity(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "edit-me", "cwd": str(tmp_path),
        })
        r = await client.post("/api/v1/agent-mail/hooks/post-tool-use", json={
            "session_id": "edit-me", "cwd": str(tmp_path),
            "tool_input": {"file_path": "/repo/foo.py"},
        })
        assert r.status_code == 200
        team = (await client.get("/api/v1/agent-mail/team")).json()["members"]
        member = next(m for m in team if any(s["session_key"] == "cc:edit-me" for s in m["sessions"]))
        session = next(s for s in member["sessions"] if s["session_key"] == "cc:edit-me")
        assert session["activity"] == "edited foo.py"
