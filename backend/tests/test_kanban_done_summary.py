# backend/tests/test_kanban_done_summary.py
"""done_summary / completed_at enrichment: pull the most recent
`**Summary:** ...` comment op from the op-log so the API/MCP can surface
it as a structured field on CardResponse.

The comment text format is fixed by mcp_server.py:184 (`Summary` is the
label for the `Done` column in `_SUMMARY_REQUIRED_COLUMNS`), and a
separate op-log search by column is wrong: the check must be on comment
text, not on column, so a card that was moved away from Done (or never
reached it) can still surface its summary if one was logged by hand.
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


@pytest.mark.asyncio
async def test_enrich_done_info_returns_pair_for_summary_comment():
    """A `**Summary:** ...` comment op produces (text, created_at)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid,
            payload={"text": "**Summary:** Built the thing and shipped it."})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at = await service.enrich_done_info(s, cid)

    assert summary == "Built the thing and shipped it."
    assert completed_at is not None


@pytest.mark.asyncio
async def test_enrich_done_info_returns_none_pair_without_summary_comment():
    """No matching comment op → (None, None), never an empty string."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        # Unrelated comment that must NOT be picked up.
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid,
            payload={"text": "Just a regular note, not a summary."})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at = await service.enrich_done_info(s, cid)

    assert summary is None
    assert completed_at is None


@pytest.mark.asyncio
async def test_enrich_done_info_returns_none_pair_for_card_without_comments():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at = await service.enrich_done_info(s, cid)

    assert summary is None
    assert completed_at is None


@pytest.mark.asyncio
async def test_enrich_done_info_picks_most_recent_summary_when_multiple():
    """Two `**Summary:**` ops → the later one (newest hlc) wins. Mirrors
    the user mental model: the latest summary is the one that reflects
    the work as it was actually shipped."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid,
            payload={"text": "**Summary:** first attempt."})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid,
            payload={"text": "**Summary:** final, polished version."})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, _ = await service.enrich_done_info(s, cid)

    assert summary == "final, polished version."


# --- Superseded detection (kaart 51813327…) ----------------------------------
# A card that goes Done → reopened → active keeps its `**Summary:**` comment in
# the op-log, so `enrich_done_info` (deliberately) still returns it. That is
# right for history (dispatch's `## REVISIT` section injects it as the *prior*
# decision) but wrong for presentation: a resuming session reads
# done_summary + completed_at + a pr-deliverable as "someone already shipped
# this" and burns tool calls proving otherwise. The staleness flag says so out
# loud instead of making every reader reverse-engineer commit hashes.


@pytest.mark.asyncio
async def test_staleness_false_for_card_still_in_done():
    """The common case: card lands in Done with a summary and stays there →
    the summary is current, so the flag is False."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("STALE-DONE", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Done", summary="Shipped it.")

    async with KanbanSessionLocal() as s:
        summary, completed_at, superseded = \
            await service.enrich_done_info_with_staleness(s, cid)

    assert summary == "Shipped it."
    assert completed_at is not None
    assert superseded is False


@pytest.mark.asyncio
async def test_staleness_true_after_reopen():
    """Done → reopened (Revisit comment + move back to Backlog) → the summary
    describes a superseded lifecycle. The text/timestamp stay available (the
    existing contract), only the flag flips."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("STALE-REOPEN", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Done", summary="Shipped v1.")
    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, cid, "Not what I asked for.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at, superseded = \
            await service.enrich_done_info_with_staleness(s, cid)

    assert summary == "Shipped v1."
    assert completed_at is not None
    assert superseded is True


@pytest.mark.asyncio
async def test_staleness_false_again_after_second_done():
    """Done → reopened → Done again with a fresh summary → the newest summary
    is current, so the flag clears. This is the card's first acceptance
    criterion: a reopened-then-redone card never presents the *old* summary."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("STALE-REDONE", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Done", summary="Shipped v1.")
    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, cid, "Redo it.")
        await s.commit()
    await m.move_card(cid, "Done", summary="Shipped v2, counterbalanced.")

    async with KanbanSessionLocal() as s:
        summary, _, superseded = \
            await service.enrich_done_info_with_staleness(s, cid)

    assert summary == "Shipped v2, counterbalanced."
    assert superseded is False


@pytest.mark.asyncio
async def test_staleness_false_for_parent_parked_in_awaiting_subtasks():
    """Parent-parking moves the card to `Awaiting Subtasks` *before* posting
    the Summary comment (mcp_server.move_card applies the move op first), and
    the park is not a reopen — so the flag must stay False. Pins both the
    op ordering and the preserving-column set."""
    from app.kanban import mcp_server as m

    parent = (await m.create_card("STALE-PARK", "parent", "",
                                  confirm_new_project=True))["id"]
    await m.create_card("STALE-PARK", "child", "", parent_card_id=parent)
    moved = await m.move_card(parent, "Done", summary="Decomposed.")
    assert moved["column"] == "Awaiting Subtasks"

    async with KanbanSessionLocal() as s:
        summary, _, superseded = \
            await service.enrich_done_info_with_staleness(s, parent)

    assert summary == "Decomposed."
    assert superseded is False


@pytest.mark.asyncio
async def test_staleness_false_without_any_summary():
    """No summary at all → nothing to be stale about."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="STALE-NONE", entity_id=None, payload={"title": "a"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at, superseded = \
            await service.enrich_done_info_with_staleness(s, cid)

    assert (summary, completed_at, superseded) == (None, None, False)


@pytest.mark.asyncio
async def test_enrich_done_info_does_not_match_other_labels():
    """`**Impediment:**` (label for the Impediment column) must NOT be
    picked up as a done_summary. The check is on the Summary label only —
    Impediment has its own column on the board and its own semantics."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid,
            payload={"text": "**Impediment:** blocked on review."})
        await s.commit()

    async with KanbanSessionLocal() as s:
        summary, completed_at = await service.enrich_done_info(s, cid)

    assert summary is None
    assert completed_at is None


# --- HTTP layer --------------------------------------------------------------
# Router-side coverage: the schema fields must surface through REST so the
# CardDrawer's green banner can read them off the response payload without a
# second round trip.

from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_card_includes_done_summary_when_done_comment_exists(_client):
    """GET /cards/{cid} surfaces done_summary and completed_at for a card with
    a `**Summary:**` comment op."""
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "DONE-SUM", "title": "t", "confirm_new_project": True})
    cid = r.json()["id"]
    # Post the summary via the MCP tool — same code path the engineer's
    # `move_card("Done", summary=...)` runs in production.
    from app.kanban import mcp_server as m
    moved = await m.move_card(cid, "Done", summary="Built it, tested it, shipped.")
    assert moved["column"] == "Done"

    r = await _client.get(f"/api/v1/kanban/cards/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["done_summary"] == "Built it, tested it, shipped."
    assert body["completed_at"] is not None


@pytest.mark.asyncio
async def test_get_card_done_summary_null_when_no_done_comment(_client):
    """No `**Summary:**` comment op → done_summary and completed_at both null."""
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "NO-SUM", "title": "t", "confirm_new_project": True})
    cid = r.json()["id"]

    r = await _client.get(f"/api/v1/kanban/cards/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["done_summary"] is None
    assert body["completed_at"] is None


@pytest.mark.asyncio
async def test_get_card_done_summary_works_when_card_moved_back_from_done(_client):
    """Acceptance criterion: the check is on comment text, not on column. A
    card that *was* in Done, accumulated a `**Summary:**` comment, and then
    got moved back to Backlog still surfaces its summary."""
    from app.kanban import mcp_server as m
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "BACK-AGAIN", "title": "t", "confirm_new_project": True})
    cid = r.json()["id"]
    await m.move_card(cid, "Done", summary="Done once.")
    await m.move_card(cid, "Backlog")  # summary comment op stays in the log

    r = await _client.get(f"/api/v1/kanban/cards/{cid}")
    body = r.json()
    assert body["column"] == "Backlog"
    assert body["done_summary"] == "Done once."
    assert body["completed_at"] is not None


@pytest.mark.asyncio
async def test_list_cards_includes_done_summary_per_card(_client):
    """GET /cards returns enriched fields on every card, not just the
    single-card endpoint. The frontend board calls list_cards and needs the
    enrichment in the same payload so a Done column doesn't have to fetch
    each card individually to render the green badge."""
    from app.kanban import mcp_server as m
    r1 = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "LIST-SUM", "title": "done", "confirm_new_project": True})
    done_id = r1.json()["id"]
    r2 = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "LIST-SUM", "title": "open"})
    open_id = r2.json()["id"]
    await m.move_card(done_id, "Done", summary="Done for the list test.")

    r = await _client.get("/api/v1/kanban/cards",
        params={"project_key": "LIST-SUM"})
    assert r.status_code == 200, r.text
    items = {c["id"]: c for c in r.json()["items"]}
    assert items[done_id]["done_summary"] == "Done for the list test."
    assert items[done_id]["completed_at"] is not None
    assert items[open_id]["done_summary"] is None
    assert items[open_id]["completed_at"] is None


# --- MCP layer ---------------------------------------------------------------
# The MCP `get_card` tool must return the enriched fields too — agents and
# scripts that talk straight to the MCP endpoint (instead of through the
# REST API) should see the same shape as the UI does.


@pytest.mark.asyncio
async def test_mcp_get_card_includes_done_summary_when_done_comment_exists():
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP-DONE", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Done", summary="Shipped from MCP.")

    card = await m.get_card(cid)
    assert card["done_summary"] == "Shipped from MCP."
    assert card["completed_at"] is not None


@pytest.mark.asyncio
async def test_mcp_get_card_done_summary_null_when_no_done_comment():
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP-NONE", "t", "", confirm_new_project=True))["id"]

    card = await m.get_card(cid)
    assert card["done_summary"] is None
    assert card["completed_at"] is None


@pytest.mark.asyncio
async def test_mcp_list_cards_includes_done_summary_per_card():
    from app.kanban import mcp_server as m

    done_id = (await m.create_card("MCP-LIST", "done", "", confirm_new_project=True))["id"]
    open_id = (await m.create_card("MCP-LIST", "open", "", confirm_new_project=True))["id"]
    await m.move_card(done_id, "Done", summary="Listed done.")

    cards = {c["id"]: c for c in await m.list_cards("MCP-LIST")}
    assert cards[done_id]["done_summary"] == "Listed done."
    assert cards[done_id]["completed_at"] is not None
    assert cards[open_id]["done_summary"] is None
    assert cards[open_id]["completed_at"] is None

@pytest.mark.asyncio
async def test_get_card_marks_done_summary_superseded_after_reopen(_client):
    """REST: a reopened card keeps its summary text but flags it as superseded,
    so a resuming session can tell an old lifecycle from the current one
    without diffing commit hashes (kaart 51813327…)."""
    from app.kanban import mcp_server as m
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST-STALE", "title": "t", "confirm_new_project": True})
    cid = r.json()["id"]
    await m.move_card(cid, "Done", summary="Shipped v1.")

    body = (await _client.get(f"/api/v1/kanban/cards/{cid}")).json()
    assert body["done_summary_superseded"] is False

    await _client.post(f"/api/v1/kanban/cards/{cid}/reopen",
                       json={"note": "Not what I asked for."})

    body = (await _client.get(f"/api/v1/kanban/cards/{cid}")).json()
    assert body["column"] == "Backlog"
    assert body["done_summary"] == "Shipped v1."
    assert body["done_summary_superseded"] is True


@pytest.mark.asyncio
async def test_list_cards_marks_done_summary_superseded_per_card(_client):
    """The board list carries the flag too, so the Done badge on a reopened
    card doesn't have to be re-derived per card."""
    from app.kanban import mcp_server as m
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST-STALE-LIST", "title": "t",
              "confirm_new_project": True})
    cid = r.json()["id"]
    await m.move_card(cid, "Done", summary="Shipped v1.")
    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, cid, "Redo.")
        await s.commit()

    items = (await _client.get(
        "/api/v1/kanban/cards", params={"project_key": "REST-STALE-LIST"},
    )).json()["items"]
    by_id = {c["id"]: c for c in items}
    assert by_id[cid]["done_summary_superseded"] is True


@pytest.mark.asyncio
async def test_mcp_get_card_marks_done_summary_superseded_after_reopen():
    """MCP `get_card` is the surface the observation was made on: the agent
    saw done_summary/completed_at from a superseded lifecycle and read it as a
    concurrent session having shipped already."""
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP-STALE", "t", "", confirm_new_project=True))["id"]
    await m.move_card(cid, "Done", summary="Shipped v1.")
    assert (await m.get_card(cid))["done_summary_superseded"] is False

    async with KanbanSessionLocal() as s:
        await service.reopen_card(s, cid, "Redo.")
        await s.commit()

    card = await m.get_card(cid)
    assert card["done_summary"] == "Shipped v1."
    assert card["done_summary_superseded"] is True
