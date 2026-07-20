"""PO-wachtrij: "Wacht op jou" — single list of all human-blocked items.

Aggregates every card state that needs a human decision before an agent can
make progress, so the product owner sees one finite, sortable list instead of
having to scan the board by column. See kanban card `c7ea21b0…`.

Four detection categories:

1. ``impediment_needs_answer`` — card on Impediment column with an open
   question (``impediment_status_for_card`` returns ``needs_answer``): either
   a ``**Impediment:**`` comment without a later ``**Resolution:**``, or an
   open KanbanGate.
2. ``gate_open`` — any KanbanGate with ``status='open'`` regardless of column.
   (In practice gates are mostly used on Impediment cards, but the gate path
   is column-independent by design.)
3. ``review_requested`` — card whose ``metadata.reviewed_card_id`` is set (a
   review-card sibling linking back to the original Done card it doubts).
4. ``awaiting_plan_ref`` — child card (has ``parent_card_id``) with no
   ``kind='plan_ref'`` deliverable: the analyst created the child but the
   follow-up ``add_plan_attachment`` has not landed yet, so the dispatcher is
   holding it out (race / stuck pipeline — the human is the only one who can
   chase the analyst if it stalls).

Sort: oldest-first (longest wait surfaces on top). Empty list when nothing is
waiting on the human.
"""
import json
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.kanban.operations import apply_operation
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


async def _make_card(s, *, project_key="PO", title="card", column="Backlog",
                     parent_card_id=None, meta=None):
    """Create a card, optionally with a parent and metadata."""
    payload = {"title": title}
    if parent_card_id:
        payload["parent_card_id"] = parent_card_id
    if meta:
        payload["metadata"] = meta
    cid = await apply_operation(
        s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload=payload,
    )
    if column != "Backlog":
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key=project_key, entity_id=cid,
            payload={"column": column},
        )
    return cid


async def _make_done_card(s, project_key="PO", title="done-card",
                          summary="Shipped."):
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload={"title": title})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Done"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid, payload={"text": f"**Summary:** {summary}"})
    return cid


async def _post_comment(s, cid, text):
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid, payload={"text": text})


async def _open_gate(s, cid, *, question="Pick one", options=("A", "B")):
    """Open a KanbanGate the same way the MCP `open_gate` tool does."""
    return await service.create_gate(
        s, card_id=cid, project_key="PO",
        question=question, options=list(options),
    )


# ---------------------------------------------------------------------------
# Service-layer detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_when_nothing_human_blocked():
    """No blockers present → empty list, not a 500 or a fabricated item."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="just sitting in Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert items == []


@pytest.mark.asyncio
async def test_impediment_with_question_appears():
    """A card on Impediment with a `**Impediment:**` question shows up."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="decide billing", column="Impediment")
        await _post_comment(s, cid,
            "**Impediment:** Postgres of SQLite voor billing?")
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert len(items) == 1
    item = items[0]
    assert item["card_id"] == cid
    assert item["kind"] == "impediment_needs_answer"
    assert "Postgres" in item["reason"]
    assert item["card_title"] == "decide billing"
    assert item["card_column"] == "Impediment"
    assert isinstance(item["wait_seconds"], (int, float))
    assert item["wait_seconds"] >= 0


@pytest.mark.asyncio
async def test_open_gate_appears():
    """A card with an open KanbanGate shows up regardless of column."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="pick provider")
        gate = await _open_gate(s, cid, question="Anthropic or Bedrock?",
                                options=("Anthropic", "Bedrock"))
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert len(items) == 1
    item = items[0]
    assert item["card_id"] == cid
    assert item["kind"] == "gate_open"
    assert "Anthropic or Bedrock?" in item["reason"]


@pytest.mark.asyncio
async def test_review_request_card_appears():
    """A review card (metadata.reviewed_card_id set) on Backlog shows up."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s, title="shipped thing")
        review_card = await service.request_review(
            s, original_id, "Edge case looks fishy to me.")
        review_id = review_card.id
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    # Only the review card lands in the queue — the original Done card stays
    # in Done and is not "waiting" on the human; the review is.
    assert len(items) == 1
    item = items[0]
    assert item["card_id"] == review_id
    assert item["kind"] == "review_requested"
    assert "Edge case" in item["reason"]


@pytest.mark.asyncio
async def test_awaiting_plan_ref_appears():
    """A child card without a plan_ref deliverable shows up."""
    async with KanbanSessionLocal() as s:
        parent_id = await _make_card(s, title="parent spike")
        child_id = await _make_card(s, title="child waits for plan",
                                    parent_card_id=parent_id)
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    # Only the child — the parent is not "waiting on a human", it's the one
    # the analyst needs to act on next.
    assert len(items) == 1
    item = items[0]
    assert item["card_id"] == child_id
    assert item["kind"] == "awaiting_plan_ref"
    assert "plan" in item["reason"].lower()


@pytest.mark.asyncio
async def test_resolved_impediment_not_in_queue():
    """An Impediment card whose `**Resolution:**` is the latest matching
    comment is `impediment_status_for_card == 'resolved'` and must NOT
    appear — the human has already answered."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="old resolved", column="Impediment")
        await _post_comment(s, cid, "**Impediment:** pick something")
        await _post_comment(s, cid, "**Resolution:** I picked A")
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert items == []


@pytest.mark.asyncio
async def test_plan_ref_present_means_child_not_blocked():
    """A child with a plan_ref deliverable is NOT waiting — the plan has
    landed, so the dispatcher can pick it up."""
    async with KanbanSessionLocal() as s:
        parent_id = await _make_card(s, title="parent")
        child_id = await _make_card(s, title="child with plan",
                                    parent_card_id=parent_id)
        # Simulate add_plan_attachment wiring (kind=plan on parent + kind=plan_ref on child)
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key="PO", entity_id=parent_id,
            payload={"plan_markdown": "# plan"},
        )
        await apply_operation(
            s, op_type="link_plan_ref", entity_type="deliverable",
            project_key="PO", entity_id=child_id,
            payload={"ref_json": json.dumps({
                "parent_card_id": parent_id, "plan_deliverable_id": "x",
            })},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert items == []


@pytest.mark.asyncio
async def test_scoped_to_project_key():
    """Cards for a different project_key are not included."""
    async with KanbanSessionLocal() as s:
        # Create blocker in OTHER project
        other_cid = await _make_card(s, project_key="OTHER",
                                      title="other blocker", column="Impediment")
        await _post_comment(s, other_cid, "**Impediment:** anything")
        # And one in PO
        po_cid = await _make_card(s, project_key="PO",
                                   title="po blocker", column="Impediment")
        await _post_comment(s, po_cid, "**Impediment:** anything")
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert len(items) == 1
    assert items[0]["card_id"] == po_cid


@pytest.mark.asyncio
async def test_sorted_oldest_first():
    """Longest-waiting item comes first.

    Both cards share the same column + kind, so the only way the wachtrij can
    distinguish them is by `wait_seconds`. We stamp a fixed `updated_at`
    difference on each by patching the helper directly: rather than rely on
    a flaky sleep, we run two passes — first create both, then patch the
    underlying `updated_at` so the older blocker has a clearly earlier
    timestamp.
    """
    from datetime import datetime, timedelta, UTC

    from app.kanban.models import KanbanCard

    async with KanbanSessionLocal() as s:
        cid_old = await _make_card(s, title="older blocker", column="Impediment")
        await _post_comment(s, cid_old, "**Impediment:** old question")
        cid_new = await _make_card(s, title="newer blocker", column="Impediment")
        await _post_comment(s, cid_new, "**Impediment:** new question")
        # Force a deterministic 1-hour gap between the two cards' updated_at
        # so the sort is independent of wall-clock timing.
        now = datetime.now(UTC).replace(tzinfo=None)
        card_old = await s.get(KanbanCard, cid_old)
        card_old.updated_at = now - timedelta(hours=1)
        await s.commit()

    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "PO")

    assert [i["card_id"] for i in items] == [cid_old, cid_new]


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_endpoint_returns_wachtrij():
    """GET /kanban/wachtrij?project_key=... returns the aggregated list."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="decide", column="Impediment")
        await _post_comment(s, cid, "**Impediment:** which stack?")
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as ac:
        resp = await ac.get("/api/v1/kanban/wachtrij?project_key=PO")

    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "impediment_needs_answer"
    assert body["items"][0]["card_id"] == cid


@pytest.mark.asyncio
async def test_rest_endpoint_empty_returns_empty_list():
    """Empty queue: items=[], total=0 — explicit, not a 404."""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as ac:
        resp = await ac.get("/api/v1/kanban/wachtrij?project_key=PO")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total": 0, "project_key": "PO"}


@pytest.mark.asyncio
async def test_rest_endpoint_unknown_project_returns_empty():
    """A project_key with no cards or columns returns empty (consistent with
    the project's "nothing registered" stance; the kanban router's broader
    404 guards are for cases the caller is *trying* to write). The wachtrij
    is a *view*, not a write — the right answer for 'no tracked items here'
    is an empty list, not a 404."""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as ac:
        resp = await ac.get(
            "/api/v1/kanban/wachtrij?project_key=ghost"
        )

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "project_key": "ghost"}
