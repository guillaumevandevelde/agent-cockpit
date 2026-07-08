import uuid

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail import (
    MailAgentSession,
    MailExternalActor,
    MailMessage,
    MailReceipt,
    MailTeamMember,
)


@pytest.mark.asyncio
async def test_create_member_session_message_receipt():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
            provider="claude-code",
        )
        s.add(session)
        await s.commit()
        await s.refresh(session)
        assert session.mailbox_status == "connected"

        actor = MailExternalActor(
            actor_key=f"openclaw-{unique}",
            display_name="OpenClaw",
            token_hash="hash",
        )
        s.add(actor)
        await s.commit()
        await s.refresh(actor)
        assert actor.kind == "external_tool"

        message = MailMessage(
            kind="message",
            sender_member_id=member.id,
            recipient_member_id=member.id,
            body_markdown="hi",
        )
        s.add(message)
        await s.commit()
        await s.refresh(message)
        assert message.request_status is None

        receipt = MailReceipt(message_id=message.id, member_id=member.id)
        s.add(receipt)
        await s.commit()
        await s.refresh(receipt)
        assert receipt.read_at is None
