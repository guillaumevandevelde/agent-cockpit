from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.agent_mail import MailAgentSession
from app.services.agent_mail_service import agent_mail_service

# Schema is created by ``_reset_app_database_tables`` in conftest.


@pytest.mark.asyncio
async def test_sync_observed_sessions_creates_member_and_session(tmp_path):
    discovered = [{
        "pane_id": "%1", "cwd": str(tmp_path), "provider": "claude-code",
        "tmux_target": "sess:0.0", "pid": 12345,
    }]
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=discovered):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)

            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%1")
            )).scalar_one()
            assert row.source == "observed"
            assert row.cli == "claude-code"
            assert row.tmux_target == "sess:0.0"
            assert row.mailbox_status == "observed"


@pytest.mark.asyncio
async def test_sync_removes_stale_observed_sessions(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[
        {"pane_id": "%1", "cwd": str(tmp_path), "provider": "claude-code", "tmux_target": "s:0.0", "pid": 1},
    ]):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)

    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            remaining = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%1")
            )).scalars().all()
            assert remaining == []