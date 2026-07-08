from unittest.mock import patch

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_list_team_reports_status_and_counts(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1")
            )
            team = await agent_mail_service.list_team(s)
            found = next(m for m in team if m.id == member.id)
            assert found.status == "connected"
            assert found.wake_state == "delivered_waiting"


@pytest.mark.asyncio
async def test_build_session_start_context_includes_identity_and_inbox(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            m1, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "a"), session_key="cc:a")
            )
            m2, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "b"), session_key="cc:b")
            )
            await agent_mail_service.send_message(
                s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="hi"),
                auto_nudge=False,
            )
            context = await agent_mail_service.build_session_start_context(s, m2.id)
            assert m2.display_name in context
            assert "1 unread" in context


@pytest.mark.asyncio
async def test_build_prompt_submit_context_none_when_empty_inbox(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:solo")
            )
            assert await agent_mail_service.build_prompt_submit_context(s, member.id) is None
