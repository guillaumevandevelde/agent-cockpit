"""Agent Mail service tests. Uses the isolated in-memory kanban DB fixture."""
import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban import mail

KanbanSessionLocal = TestSessionLocal()
PK = "slug:proj"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_identity_is_durable_across_sessions():
    async with KanbanSessionLocal() as s:
        a = await mail.ensure_identity(s, PK, "developer", agent_session="k-x-1111")
        await s.commit()
        first_id = a.id
        assert a.last_session == "k-x-1111"

        # A new developer session reuses the same durable identity row.
        b = await mail.ensure_identity(s, PK, "developer", agent_session="k-x-2222")
        await s.commit()
        assert b.id == first_id
        assert b.last_session == "k-x-2222"

        ids = await mail.list_identities(s, PK)
        assert [i.handle for i in ids] == ["developer"]


@pytest.mark.asyncio
async def test_send_and_inbox_directed_and_broadcast():
    async with KanbanSessionLocal() as s:
        await mail.send_message(s, PK, "analyst", "developer", "note", "hi", "body")
        await mail.send_message(s, PK, "analyst", None, "note", "team", "all")
        await mail.send_message(s, PK, "analyst", "testing", "note", "other", "x")
        await s.commit()

        dev = await mail.list_inbox(s, PK, "developer")
        assert {m.subject for m in dev} == {"hi", "team"}  # directed + broadcast

        dev_no_bcast = await mail.list_inbox(s, PK, "developer", include_broadcast=False)
        assert {m.subject for m in dev_no_bcast} == {"hi"}


@pytest.mark.asyncio
async def test_context_response_marks_request_answered():
    async with KanbanSessionLocal() as s:
        req = await mail.send_message(s, PK, "developer", "analyst",
            "context_request", "How?", "explain the flow")
        await s.commit()
        assert req.status == "unread"

        resp = await mail.send_message(s, PK, "analyst", "developer",
            "context_response", "Re: How?", "here", in_reply_to=req.id)
        await s.commit()

        refreshed = await mail.get_message(s, req.id)
        assert refreshed.status == "answered"
        assert resp.in_reply_to == req.id

        thread = await mail.list_thread(s, req.id)
        assert [m.id for m in thread] == [req.id, resp.id]


@pytest.mark.asyncio
async def test_mark_read_only_for_recipient():
    async with KanbanSessionLocal() as s:
        msg = await mail.send_message(s, PK, "analyst", "developer", "note", "s", "b")
        await s.commit()

        # Wrong reader: no state change.
        untouched = await mail.mark_read(s, msg.id, "testing")
        await s.commit()
        assert untouched.status == "unread"
        assert untouched.read_at is None

        read = await mail.mark_read(s, msg.id, "developer")
        await s.commit()
        assert read.status == "read"
        assert read.read_at is not None


@pytest.mark.asyncio
async def test_pending_for_card_only_unread_handoff_and_requests():
    async with KanbanSessionLocal() as s:
        await mail.send_message(s, PK, "analyst", "developer", "handoff",
            "take over", "context", card_id="card-1")
        await mail.send_message(s, PK, "analyst", "developer", "context_request",
            "q", "?", card_id="card-1")
        await mail.send_message(s, PK, "analyst", "developer", "note",
            "fyi", "n", card_id="card-1")  # notes excluded
        await mail.send_message(s, PK, "analyst", "developer", "handoff",
            "other card", "c", card_id="card-2")  # other card excluded
        await s.commit()

        pending = await mail.pending_for_card(s, PK, "card-1", "developer")
        assert {m.subject for m in pending} == {"take over", "q"}

        # Marking one read drops it from pending.
        await mail.mark_read(s, pending[0].id, "developer")
        await s.commit()
        pending2 = await mail.pending_for_card(s, PK, "card-1", "developer")
        assert pending[0].subject not in {m.subject for m in pending2}


@pytest.mark.asyncio
async def test_send_message_rejects_unknown_kind():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await mail.send_message(s, PK, "analyst", "developer", "bogus", "s", "b")
