# backend/tests/test_kanban_request_review.py
"""request_review: flag doubt on a Done card and route it to the analyst.

A human who doubts a shipped implementation posts a `**Review requested:** <note>`
comment on the original card and spawns a new `work_type="analysis"` card that
auto-routes to the analyst persona and links back via metadata.reviewed_card_id.
The original card is left intact (a new card, not a reopen), and the action is
rejected (409 / error dict) unless the card is currently in Done.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch, service
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


async def _make_done_card(s, project_key="P", title="Ship the thing",
                          summary="Built it, tested it, shipped."):
    """Create a card, move it to Done with a summary comment, and return its id."""
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload={"title": title})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Done"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid, payload={"text": f"**Summary:** {summary}"})
    await s.commit()
    return cid


# --- Service layer -----------------------------------------------------------


@pytest.mark.asyncio
async def test_request_review_creates_analysis_card_linked_to_original():
    """Acceptance (a): exactly one new card with work_type=analysis and
    metadata.reviewed_card_id == original.id."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        review = await service.request_review(s, original_id, "I doubt the edge case is handled.")
        await s.commit()

    assert review.work_type == "analysis"
    assert review.meta == {"reviewed_card_id": original_id}
    assert review.column == "Backlog"
    assert review.title == "Review: Ship the thing"
    # work_type="analysis" auto-routes to the analyst persona.
    assert review.agent == "analyst"
    # The doubt + original summary flow into the review card's description.
    assert "I doubt the edge case is handled." in review.description
    assert "Built it, tested it, shipped." in review.description

    async with KanbanSessionLocal() as s:
        cards = await service.list_cards(s, "P")
    analysis_cards = [c for c in cards if c.work_type == "analysis"]
    assert len(analysis_cards) == 1


@pytest.mark.asyncio
async def test_request_review_posts_prefixed_comment_on_original():
    """Acceptance (b): the original card's activity feed contains the
    `**Review requested:**`-prefixed comment."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        await service.request_review(s, original_id, "Please double-check the migration.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, original_id)
    comments = [op.payload.get("text", "") for op in activity if op.op_type == "comment"]
    assert any(t.startswith("**Review requested:** ") and "migration" in t for t in comments)


@pytest.mark.asyncio
async def test_request_review_does_not_disturb_original_done_summary():
    """The original card stays in Done and keeps its done_summary — a review is
    a new card, not a reopen. Also guards against the review comment being
    mistaken for the Done summary (distinct prefix)."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s, summary="Original summary text.")

    async with KanbanSessionLocal() as s:
        await service.request_review(s, original_id, "Some doubt.")
        await s.commit()

    async with KanbanSessionLocal() as s:
        original = await service.get_card(s, original_id)
        summary, _ = await service.enrich_done_info(s, original_id)
    assert original.column == "Done"
    assert summary == "Original summary text."


@pytest.mark.asyncio
async def test_request_review_rejects_non_done_card():
    """Acceptance (c) at the service layer: a card not in Done raises CardNotInDone."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="P", entity_id=None, payload={"title": "wip"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        with pytest.raises(service.CardNotInDone):
            await service.request_review(s, cid, "too early")


@pytest.mark.asyncio
async def test_request_review_missing_card_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.request_review(s, "does-not-exist", "n") is None


@pytest.mark.asyncio
async def test_request_review_includes_deliverable_refs_in_description():
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=original_id,
            payload={"kind": "branch", "ref": "k-some-branch"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        review = await service.request_review(s, original_id, "check it")
        await s.commit()
    assert "branch: k-some-branch" in review.description


# --- Priority dispatch contract ---------------------------------------------
#
# Acceptance criterion from kanban card b4710c5a… (self-improve): a human in
# the loop asked for a review on a Done card and waited >1h because the new
# review card sat behind ~20 Backlog cards. Without `priority="high"` it sorts
# behind everything (rank-based FIFO); with it the dispatcher's `_next_card`
# picks it before lower-priority work. Three things must hold:
#   (1) the review card carries `priority="high"` (service contract),
#   (2) it sits in the priority-sorted dispatch order ahead of older
#       Backlog cards with no priority (rank would otherwise beat it),
#   (3) the dispatcher picks it up as the very first thing.


@pytest.mark.asyncio
async def test_request_review_sets_high_priority_on_new_card():
    """Service contract: a review card created via request_review carries
    priority='high' so it jumps the queue. Without this, a human in the loop
    waits as long as the rank-FIFO backlog (1h+ in the worst observed case),
    and falls back to reopening the source card as the costliest possible
    corrective action (a full Opus re-analysis)."""
    async with KanbanSessionLocal() as s:
        original_id = await _make_done_card(s)

    async with KanbanSessionLocal() as s:
        review = await service.request_review(s, original_id, "doubt")
        await s.commit()

    assert review.priority == "high", (
        "request_review must set priority='high' so the review card sorts "
        "ahead of ordinary Backlog cards via dispatch._priority_key"
    )


@pytest.mark.asyncio
async def test_request_review_card_dispatches_before_older_unprioritised_backlog():
    """Integration: even when an unprioritised Backlog card was filed *first*
    (older rank) and the review card only lands later, `_next_card` returns
    the review card. This is the exact scenario that produced the 1h+ wait."""
    PK = "P"
    async with KanbanSessionLocal() as s:
        # File an ordinary Backlog card first so it has an older rank than the
        # review card will have when we create it next.
        await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "filed-an-hour-ago", "column": "Backlog"},
        )
        original_id = await _make_done_card(s, project_key=PK)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await service.request_review(s, original_id, "please check")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await service.list_cards(s, PK)
    backlog = [c for c in cards if c.column == "Backlog"]
    assert {c.title for c in backlog} == {"filed-an-hour-ago", "Review: Ship the thing"}

    async with KanbanSessionLocal() as s:
        cards = await service.list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "Review: Ship the thing", (
        "the review card must be picked first even though rank would pick the older card"
    )


# --- REST layer --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_request_review_creates_linked_card(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "Feature Y", 'confirm_new_project': True})
    original_id = r.json()["id"]
    from app.kanban import mcp_server as m
    await m.move_card(original_id, "Done", summary="Y is done.")

    r = await _client.post(f"/api/v1/kanban/cards/{original_id}/request-review",
        json={"note": "The API contract looks off."})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["work_type"] == "analysis"
    assert body["metadata"] == {"reviewed_card_id": original_id}
    assert body["title"] == "Review: Feature Y"

    # The original card carries the prefixed comment.
    act = await _client.get(f"/api/v1/kanban/cards/{original_id}/activity")
    texts = [e["payload"].get("text", "") for e in act.json() if e["op_type"] == "comment"]
    assert any(t.startswith("**Review requested:** ") for t in texts)


@pytest.mark.asyncio
async def test_rest_request_review_409_on_non_done_card(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "Not done yet", 'confirm_new_project': True})
    cid = r.json()["id"]

    r = await _client.post(f"/api/v1/kanban/cards/{cid}/request-review",
        json={"note": "premature"})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_rest_request_review_404_on_missing_card(_client):
    r = await _client.post("/api/v1/kanban/cards/nope/request-review",
        json={"note": "x"})
    assert r.status_code == 404, r.text


# --- MCP layer ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_request_review_creates_linked_card():
    from app.kanban import mcp_server as m

    original_id = (await m.create_card("MCP", "Ship Z", "", confirm_new_project=True))["id"]
    await m.move_card(original_id, "Done", summary="Z shipped.")

    review = await m.request_review(original_id, "Z might leak a handle.")
    assert review["work_type"] == "analysis"
    assert review["metadata"] == {"reviewed_card_id": original_id}


@pytest.mark.asyncio
async def test_mcp_request_review_error_on_non_done_card():
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP", "wip", "", confirm_new_project=True))["id"]
    res = await m.request_review(cid, "too early")
    assert res["error"] == "not_in_done"
    assert res["column"] == "Backlog"
