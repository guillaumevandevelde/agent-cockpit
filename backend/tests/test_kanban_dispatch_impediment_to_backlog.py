# backend/tests/test_kanban_dispatch_impediment_to_backlog.py
"""`dispatch_impediment_card` must move a resolved card to **Backlog** (not
Doing) so the auto-dispatch tick picks it up at a controlled pace, instead of
spawning a fresh session synchronously the moment a human clicks "Resolve
impediment".

This is the contract enforced by kaart af951ad70... ("Resolve impediment moet
niet meteen naar engineer kolom maar eerst naar backlog"): resolving a batch
of impediments must not start a batch of fresh sessions — the cards sit in
Backlog until the next auto-tick picks them up via the normal flow.

We exercise the real `dispatch_impediment_card` (not the router-only mock the
upstream tests use). The `RecordingTransport` is a no-op stand-in that records
the spawn call; assertions check (a) the card lands in Backlog, (b) no session
was spawned at resolve-time. The auto-tick follow-up is exercised by the
neighbouring `test_kanban_dispatch.py` `dispatch_project` test.
"""
import pytest
import pytest_asyncio

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


KanbanSessionLocal = TestSessionLocal()


class RecordingTransport:
    """Same shape as `test_kanban_dispatch.RecordingTransport`. Duplicated here
    so this test stays self-contained — the upstream helper is private to
    test_kanban_dispatch.py and importing across modules pulls a larger test
    surface than this card needs.
    """
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "cli_id": cli_id,
                           "provider": provider, "model": model})
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


async def _make_impediment_card(s):
    """Create + park a card on Impediment with a question comment."""
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=PK, entity_id=None,
        payload={"title": "blocked task", "column": "Backlog"})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Impediment"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid,
        payload={"text": "**Impediment:** Which library should we use?"})
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_dispatch_impediment_card_moves_card_to_backlog_not_doing():
    """AC #1: resolving an impediment moves the card to Backlog so auto-dispatch
    picks it up next tick, instead of jumping straight to Doing (which the
    dispatcher's tick never scans from — Doing is owned by the per-agent
    in-flight slot, not by the backlog queue)."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        await s.commit()

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_impediment_card(
            s, card_id=cid, project_path="/tmp",
            target_agent="engineer",
            impediment_question="Which library should we use?",
            impediment_answer="Use library B.",
            transport=transport,
        )
        await s.commit()

    # The card is now on Backlog (not Doing / not any agent column).
    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Backlog", (
        f"expected Backlog after resolve, got {card.column!r} — card was sent "
        f"straight to an agent column, which floods the dispatcher"
    )

    # And no session was spawned synchronously: the transport was never called.
    assert transport.calls == [], (
        f"resolve-impediment spawned {len(transport.calls)} session(s); "
        f"expected 0 — auto-dispatch should drive the spawn"
    )

    # `dispatch_impediment_card` still returns a non-None marker so the router's
    # existing `res is None → 409 Conflict` branch keeps working (the marker
    # shape is documented as `{"session_name": None}` so callers don't try to
    # mint one of their own).
    assert res is not None
    assert res.get("session_name") is None


@pytest.mark.asyncio
async def test_dispatch_impediment_card_no_op_when_card_not_in_impediment():
    """Defensive guard: if the card has already moved off Impediment (e.g. a
    concurrent move), `dispatch_impediment_card` must return None and not
    spawn a session — preserves the 409-Conflict contract upstream."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "not blocked", "column": "Backlog"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_impediment_card(
            s, card_id=cid, project_path="/tmp",
            target_agent="engineer",
            impediment_question="ignored",
            transport=transport,
        )
        await s.commit()

    assert res is None
    assert transport.calls == []

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Backlog", "card must not be moved by a no-op resolve"


@pytest.mark.asyncio
async def test_dispatch_impediment_card_returns_none_when_card_missing():
    """A missing card must NOT raise — the upstream router turns None into a
    409 Conflict."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_impediment_card(
            s, card_id="non-existent", project_path="/tmp",
            target_agent="engineer",
            impediment_question="ignored",
            transport=transport,
        )
    assert res is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dispatch_project_threads_impediment_context_into_prompt():
    """AC #2: once the card sits in Backlog with `**Impediment:**` /
    `**Resolution:**` comments on the activity feed, the auto-tick that picks
    it up must extract those comments and render the `## IMPEDIMENT` prompt
    section (question + authoritative answer) — so the resumed session acts
    on the human's decision instead of starting blind.

    Without this, dropping the card in Backlog would silently lose the
    impediment context the human just provided.
    """
    async with KanbanSessionLocal() as s:
        # Card already in Backlog (the post-resolve state), with both comments
        # posted by the human + the prior blocked session.
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "needs lib pick", "column": "Backlog"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": "**Impediment:** Postgres or SQLite?"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": "**Resolution:** Use SQLite."})
        await s.commit()

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    prompt = transport.calls[0]["prompt"]
    assert "## IMPEDIMENT" in prompt, (
        "auto-tick must render the IMPEDIMENT section so the resumed session "
        "sees the human's decision; got prompt:\n" + prompt[:500]
    )
    assert "Postgres or SQLite?" in prompt
    assert "Use SQLite." in prompt
    # The answer is rendered as authoritative (the human's binding decision).
    assert "authoritative" in prompt


@pytest.mark.asyncio
async def test_dispatch_project_renders_question_only_when_no_resolution():
    """AC #3: a Backlog card with only an `**Impediment:**` comment (no
    `**Resolution:**` yet — the rare path where resolve-impediment is called
    without an answer and no structured gate existed either) still gets the
    IMPEDIMENT section rendered, framed as an open question rather than an
    authoritative decision."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "needs clarification", "column": "Backlog"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": "**Impediment:** Which library?"})
        await s.commit()

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    prompt = transport.calls[0]["prompt"]
    assert "## IMPEDIMENT" in prompt
    assert "Which library?" in prompt
    # No authoritative answer — the prompt frames it as an open question.
    assert "authoritative" not in prompt
    assert "clarify what's needed" in prompt


@pytest.mark.asyncio
async def test_dispatch_project_does_not_render_impediment_section_when_no_comments():
    """Regression guard: a Backlog card with NO impediment comments (the common
    case for ordinary cards) must NOT spuriously render the IMPEDIMENT
    section — the prompt would be polluted for every card on the board."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "ordinary task", "column": "Backlog"})
        await s.commit()

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    assert "## IMPEDIMENT" not in transport.calls[0]["prompt"]