from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.agent_mail import MailTeamMember
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service

# Schema is created by ``_reset_app_database_tables`` in conftest.


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


@pytest.mark.asyncio
async def test_build_session_start_context_gcs_members_whose_repo_path_no_longer_exists(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            gone = tmp_path / "gone"
            gone.mkdir()
            gone_member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(gone), session_key="cc:gone")
            )
            live = tmp_path / "live"
            live.mkdir()
            live_member, session = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(live), session_key="cc:live")
            )
            gone.rmdir()

            context = await agent_mail_service.build_session_start_context(
                s, live_member.id, session.session_key,
            )
            assert gone_member.display_name not in context

            remaining = (await s.execute(select(MailTeamMember))).scalars().all()
            assert all(m.id != gone_member.id for m in remaining)
            assert any(m.id == live_member.id for m in remaining)


@pytest.mark.asyncio
async def test_build_session_start_context_includes_identity_and_inbox(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            (tmp_path / "a").mkdir()
            (tmp_path / "b").mkdir()
            m1, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "a"), session_key="cc:a")
            )
            m2, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "b"), session_key="cc:b")
            )
            await agent_mail_service.send_message(
                s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="hi"),
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
