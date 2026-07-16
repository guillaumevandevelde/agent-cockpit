import pytest

from app.database import Base
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service
from tests.agent_mail_test_db import AsyncSessionLocal, engine


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _two_members(s, tmp_path):
    m1, _ = await agent_mail_service.register_session(
        s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "a"), session_key="cc:a")
    )
    m2, _ = await agent_mail_service.register_session(
        s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "b"), session_key="cc:b")
    )
    return m1, m2


@pytest.mark.asyncio
async def test_send_direct_message_creates_receipt_for_recipient(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        msg = await agent_mail_service.send_message(
            s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="hi"),
            auto_nudge=False,
        )
        assert msg.sender_name == m1.display_name
        inbox = await agent_mail_service.get_inbox(s, m2.id)
        assert inbox.unread_count == 1
        assert inbox.messages[0].body_markdown == "hi"


@pytest.mark.asyncio
async def test_broadcast_reaches_all_other_members(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        await agent_mail_service.send_message(
            s, MailMessageCreate(kind="broadcast", sender_member_id=m1.id, body_markdown="hello team"),
            auto_nudge=False,
        )
        inbox = await agent_mail_service.get_inbox(s, m2.id)
        assert inbox.unread_count >= 1
        assert any(m.body_markdown == "hello team" for m in inbox.messages)


@pytest.mark.asyncio
async def test_answer_marks_context_request_answered(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        req = await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="context_request", sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="?",
            ), auto_nudge=False,
        )
        assert req.request_status == "pending"

        await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="answer", sender_member_id=m2.id, thread_root_id=req.id, body_markdown="answer",
            ), auto_nudge=False,
        )
        thread = await agent_mail_service.get_thread(s, req.id)
        assert thread.root.request_status == "answered"
        assert thread.replies[0].kind == "answer"


@pytest.mark.asyncio
async def test_mark_read_and_ack_message(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        msg = await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="handoff", sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="take over",
            ), auto_nudge=False,
        )
        await agent_mail_service.mark_read(s, msg.id, m2.id)
        await agent_mail_service.ack_message(s, msg.id, m2.id)

        thread = await agent_mail_service.get_thread(s, msg.id, for_member_id=m2.id)
        assert thread.root.read_at is not None
        assert thread.root.acked_at is not None
        assert thread.root.request_status == "acknowledged"


@pytest.mark.asyncio
async def test_answer_requires_pending_context_request_addressed_to_sender(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        note = await agent_mail_service.send_message(
            s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="note"),
            auto_nudge=False,
        )
        with pytest.raises(ValueError, match="answer messages can only resolve context requests"):
            await agent_mail_service.send_message(
                s, MailMessageCreate(
                    kind="answer", sender_member_id=m2.id, thread_root_id=note.id, body_markdown="x",
                ), auto_nudge=False,
            )
