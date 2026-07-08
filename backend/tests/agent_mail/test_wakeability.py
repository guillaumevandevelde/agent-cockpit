from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail import MailAgentSession
from app.models.agent_mail_schemas import MailAgentRegisterRequest
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _discovered_pane(pane_id, tmp_path, provider="claude-code"):
    return [{"pane_id": pane_id, "cwd": str(tmp_path), "provider": provider, "tmux_target": "sess:0.0", "pid": 999}]


@pytest.mark.asyncio
async def test_queue_inbox_check_sends_tmux_text(tmp_path):
    # queue_inbox_check re-syncs observed sessions before waking, so the discovery
    # mock must keep reporting the pane as live (not return []) for the wake to see it.
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=_discovered_pane("%q1", tmp_path)), \
         patch("app.services.agent_mail_service.send_text", return_value=True) as mock_send:
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%q1")
            )).scalar_one()
            member_id = row.member_id

            result = await agent_mail_service.queue_inbox_check(s, member_id)
            assert result["method"] == "tmux"
            assert result["target"] == "sess:0.0"
            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "sess:0.0"


@pytest.mark.asyncio
async def test_queue_inbox_check_raises_when_not_wakeable(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:offline")
            )
            with pytest.raises(ValueError, match="No Agent Mail wake path"):
                await agent_mail_service.queue_inbox_check(s, member.id)


@pytest.mark.asyncio
async def test_auto_nudge_respects_cooldown(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=_discovered_pane("%q2", tmp_path)), \
         patch("app.services.agent_mail_service.send_text", return_value=True) as mock_send:
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%q2")
            )).scalar_one()
            member_id = row.member_id

            nudged1 = await agent_mail_service.auto_nudge_members(s, {member_id})
            nudged2 = await agent_mail_service.auto_nudge_members(s, {member_id})
            assert len(nudged1) == 1
            assert len(nudged2) == 0  # cooldown
            assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_wake_members_with_results_reports_offline(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:y")
            )
            results = await agent_mail_service.wake_members_with_results(s, {member.id})
            assert results[member.id]["wake_attempted"] is False
            assert results[member.id]["wake_succeeded"] is False
