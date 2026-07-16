import pytest
from httpx import ASGITransport, AsyncClient

import app.database as database_module
from app.database import Base
from app.main import app
from tests.agent_mail_test_db import AsyncSessionLocal as agent_mail_session_factory
from tests.agent_mail_test_db import engine


@pytest.fixture(autouse=True)
async def _create_tables(monkeypatch):
    # get_db() looks up AsyncSessionLocal from app.database's own module
    # globals at call time, so patching the attribute here (rather than
    # wherever a router imported get_db from) redirects every DB session
    # opened through the real ASGI app for the duration of this test.
    monkeypatch.setattr(database_module, "AsyncSessionLocal", agent_mail_session_factory)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
