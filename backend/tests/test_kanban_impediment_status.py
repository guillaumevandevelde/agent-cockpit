# backend/tests/test_kanban_impediment_status.py
"""Impediment-lane classification.

`impediment_status_for_card` derives a tiny categorical label for the
Impediment column so the UI can show "needs answer", "dispatch failed" (with
a Redispatch quick-action), or "no question" at a glance instead of treating
every Impediment card as "needs a human answer". See kanban card `c5eb6f89`.

The classification is built from already-persisted signals:
  * an open KanbanGate row → "needs_answer"
  * the dispatch-failure auto-move comment → "dispatch_failed"
  * a `**Impediment:**` comment without a later `**Resolution:**`
                                       → "needs_answer"
  * a `**Resolution:**` comment that arrived AFTER the latest impediment
    comment → "resolved" (the card is still on Impediment briefly during the
    resolve-impediment flow — flag it as "resolved" so the UI doesn't keep
    showing it as actionable)
  * none of the above on an Impediment card → "no_question"

Cards not in the Impediment column return None so the field is a clean
nullable, keeping the existing CardResponse shape untouched for everyone
else.
"""
import pytest
import pytest_asyncio

from app.kanban import service
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_imp_card(s, *, prefix_text: list[str] | None = None) -> str:
    """Create a card, move it to Impediment, optionally post some comments.

    Posts the comments in the given order so the test can shape the activity
    feed to its needs. Returns the card id.
    """
    cid = await apply_operation(
        s, op_type="create", entity_type="card",
        project_key="IMP", entity_id=None, payload={"title": "imp"},
    )
    await apply_operation(
        s, op_type="move", entity_type="card",
        project_key="IMP", entity_id=cid, payload={"column": "Impediment"},
    )
    for t in prefix_text or []:
        await apply_operation(
            s, op_type="comment", entity_type="comment",
            project_key="IMP", entity_id=cid, payload={"text": t},
        )
    return cid


@pytest.mark.asyncio
async def test_returns_none_for_non_impediment_card():
    """A card outside Impediment returns None so the field is null on the wire."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="IMP", entity_id=None, payload={"title": "backlog"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status is None


@pytest.mark.asyncio
async def test_impediment_without_any_signal_is_no_question():
    """A bare-move to Impediment (no comment, no gate) is the third 'wees'
    category. The UI uses this to avoid suggesting an answer is needed."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "no_question"


@pytest.mark.asyncio
async def test_impediment_comment_is_needs_answer():
    """A `**Impediment:** <q>` comment without a structured gate still says
    'needs answer' — the free-text legacy path."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "**Impediment:** Postgres or SQLite?",
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "needs_answer"


@pytest.mark.asyncio
async def test_open_kanban_gate_is_needs_answer():
    """A structured-options gate (open status) is the strongest 'still pending'
    signal — classified as needs_answer regardless of any prior comment."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "**Impediment:** need a database choice",
        ])
        await service.create_gate(
            s, card_id=cid, project_key="IMP",
            question="Postgres or SQLite?", options=["Postgres", "SQLite"],
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "needs_answer"


@pytest.mark.asyncio
async def test_dispatch_failure_auto_move_comment_is_dispatch_failed():
    """The exact auto-move prose written by `_move_to_impediment_after_repeated_failures`
    uses a `[dispatch-failure]` marker prefix so this detection is
    deterministic, not prose-fragile."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "[dispatch-failure] Session `k-abc12345` failed to dispatch 3 "
            "times in a row — moved to Impediment instead of retrying again.",
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "dispatch_failed"


@pytest.mark.asyncio
async def test_dispatch_failure_after_impediment_comment_wins():
    """The latest signal in the feed wins. A dispatch-failure comment that
    landed AFTER a `**Impediment:**` question (e.g. human-decision card that
    then failed to redispatch three times) is classified as dispatch_failed —
    the human's question was already moot by the time the second failure
    happened."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "**Impediment:** Postgres or SQLite?",
            "[dispatch-failure] Session `k-abc12345` failed to dispatch 3 "
            "times in a row — moved to Impediment.",
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "dispatch_failed"


@pytest.mark.asyncio
async def test_resolution_comment_after_impediment_is_resolved():
    """Once a `**Resolution:**` lands after the `**Impediment:**` comment,
    the impediment is no longer pending. Returns "resolved" so the UI can
    distinguish this transient state (card still on Impediment during the
    click-through to "Resolve impediment") from a fresh no_question."""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "**Impediment:** which logger?",
            "**Resolution:** go with structlog.",
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "resolved"


@pytest.mark.asyncio
async def test_impediment_comment_after_resolution_is_needs_answer_again():
    """A re-impediment after a resolution — the latest `**Impediment:**` in
    the feed — re-flips the card to needs_answer. (Defensive: the Resolved
    state should normally be transient, but a stuck card could plausibly
    loop back here.)"""
    async with KanbanSessionLocal() as s:
        cid = await _make_imp_card(s, prefix_text=[
            "**Impediment:** which logger?",
            "**Resolution:** go with structlog.",
            "**Impediment:** actually, use loguru",
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        status = await service.impediment_status_for_card(s, card)

    assert status == "needs_answer"


# --- HTTP / MCP layer --------------------------------------------------------
# Router- and MCP-side coverage, mirroring the `test_kanban_done_summary.py`
# pattern: the enrichment must surface on `GET /cards`, the single-card get,
# and the MCP equivalent so any kanban consumer sees the new field without a
# second round trip.

from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_card_includes_impediment_status_dispatch_failed(_client):
    """`GET /cards/{cid}` surfaces `impediment_status` for an Impediment card
    that landed there via 3× dispatch failure."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("IMP-HTTP-1", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Impediment")
    # Simulate the auto-move comment + a successor move (same effect as
    # _move_to_impediment_after_repeated_failures produces in production).
    await apply_operation(
        TestSessionLocal()(), op_type="comment", entity_type="comment",
        project_key="IMP-HTTP-1", entity_id=cid,
        payload={"text":
                 "[dispatch-failure] Session `k-zzz` failed to dispatch 3 "
                 "times in a row — moved to Impediment instead of retrying."},
    )

    r = await _client.get(f"/api/v1/kanban/cards/{cid}")
    assert r.status_code == 200, r.text
    assert r.json()["impediment_status"] == "dispatch_failed"


@pytest.mark.asyncio
async def test_get_card_includes_impediment_status_needs_answer(_client):
    """A `**Impediment:**` comment + open gate → needs_answer."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("IMP-HTTP-2", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Impediment")
    await apply_operation(
        TestSessionLocal()(), op_type="comment", entity_type="comment",
        project_key="IMP-HTTP-2", entity_id=cid,
        payload={"text": "**Impediment:** which DB?"},
    )

    r = await _client.get(f"/api/v1/kanban/cards/{cid}")
    assert r.json()["impediment_status"] == "needs_answer"


@pytest.mark.asyncio
async def test_list_cards_includes_impediment_status_per_card(_client):
    """Every card on the board surfaces its status; non-Impediment cards get null."""
    from app.kanban import mcp_server as m

    no_q_id = (await m.create_card("IMP-LIST", "bare", "", confirm_new_project=True))["id"]
    await m.move_card(no_q_id, "Impediment")
    ask_id = (await m.create_card("IMP-LIST", "ask", "", confirm_new_project=True))["id"]
    await m.move_card(ask_id, "Impediment")
    await apply_operation(
        TestSessionLocal()(), op_type="comment", entity_type="comment",
        project_key="IMP-LIST", entity_id=ask_id,
        payload={"text": "**Impediment:** pick A or B"},
    )
    back_id = (await m.create_card("IMP-LIST", "back", "", confirm_new_project=True))["id"]
    # Stays on Backlog.

    r = await _client.get("/api/v1/kanban/cards",
                         params={"project_key": "IMP-LIST"})
    assert r.status_code == 200, r.text
    items = {c["id"]: c for c in r.json()["items"]}
    assert items[no_q_id]["impediment_status"] == "no_question"
    assert items[ask_id]["impediment_status"] == "needs_answer"
    assert items[back_id]["impediment_status"] is None


@pytest.mark.asyncio
async def test_mcp_list_cards_includes_impediment_status_per_card():
    """MCP layer parity: agent-side tools see the same field as REST."""
    from app.kanban import mcp_server as m

    no_q_id = (await m.create_card("MCP-IMP-LIST", "bare", "", confirm_new_project=True))["id"]
    await m.move_card(no_q_id, "Impediment")
    fail_id = (await m.create_card("MCP-IMP-LIST", "fail", "", confirm_new_project=True))["id"]
    await m.move_card(fail_id, "Impediment")
    await apply_operation(
        TestSessionLocal()(), op_type="comment", entity_type="comment",
        project_key="MCP-IMP-LIST", entity_id=fail_id,
        payload={"text":
                 "[dispatch-failure] Session `k-aaaa` failed to dispatch 3 times in a row."},
    )

    cards = {c["id"]: c for c in await m.list_cards("MCP-IMP-LIST")}
    assert cards[no_q_id]["impediment_status"] == "no_question"
    assert cards[fail_id]["impediment_status"] == "dispatch_failed"
