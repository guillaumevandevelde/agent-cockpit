import uuid

import pytest

from app.database import AsyncSessionLocal
from app.models.agent_mail import MailAgentSession, MailTeamMember

# Schema is created by ``_reset_app_database_tables`` in conftest.


@pytest.mark.asyncio
async def test_create_member_and_session():
    unique = uuid.uuid4().hex[:12]
    async with AsyncSessionLocal() as s:
        member = MailTeamMember(
            identity_key=f"repo:{unique}",
            repo_id=unique,
            repo_path="/home/x/repo",
            repo_name="repo",
            display_name="repo",
        )
        s.add(member)
        await s.commit()
        await s.refresh(member)
        assert member.id is not None
        assert member.created_at is not None

        session = MailAgentSession(
            member_id=member.id,
            source="hook",
            session_key=f"cc:sess-{unique}",
            cli="claude-code",
        )
        s.add(session)
        await s.commit()
        await s.refresh(session)
        assert session.mailbox_status == "connected"
