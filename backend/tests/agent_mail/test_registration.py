import pytest

from app.database import Base
from app.models.agent_mail_schemas import MailAgentRegisterRequest
from app.services.agent_mail_service import agent_mail_service
from tests.agent_mail_test_db import AsyncSessionLocal, engine


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_register_session_creates_member_and_session(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(
            source="hook", provider="claude-code",
            cwd=str(tmp_path), session_key="cc:sess-1",
        )
        member, session = await agent_mail_service.register_session(s, req)
        assert member.repo_name == tmp_path.name
        assert session.session_key == "cc:sess-1"
        assert session.mailbox_status == "connected"


@pytest.mark.asyncio
async def test_register_session_same_cwd_reuses_member(tmp_path):
    async with AsyncSessionLocal() as s:
        req1 = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1")
        req2 = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:2")
        member1, _ = await agent_mail_service.register_session(s, req1)
        member2, _ = await agent_mail_service.register_session(s, req2)
        assert member1.id == member2.id


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen_and_activity(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:hb")
        _, session = await agent_mail_service.register_session(s, req)
        first_seen = session.last_seen_at

        updated = await agent_mail_service.heartbeat_session(s, "cc:hb", activity="edited foo.py")
        assert updated is not None
        assert updated.activity == "edited foo.py"
        assert updated.last_seen_at >= first_seen


@pytest.mark.asyncio
async def test_mark_session_offline(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:off")
        await agent_mail_service.register_session(s, req)
        await agent_mail_service.mark_session_offline(s, "cc:off")

        from sqlalchemy import select

        from app.models.agent_mail import MailAgentSession
        row = (await s.execute(
            select(MailAgentSession).where(MailAgentSession.session_key == "cc:off")
        )).scalar_one()
        assert row.mailbox_status == "offline"
