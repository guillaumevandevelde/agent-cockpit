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


# --- REST layer --------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_request_review_creates_linked_card(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "Feature Y"})
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
        json={"project_key": "REST", "title": "Not done yet"})
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

    original_id = (await m.create_card("MCP", "Ship Z", ""))["id"]
    await m.move_card(original_id, "Done", summary="Z shipped.")

    review = await m.request_review(original_id, "Z might leak a handle.")
    assert review["work_type"] == "analysis"
    assert review["metadata"] == {"reviewed_card_id": original_id}


@pytest.mark.asyncio
async def test_mcp_request_review_error_on_non_done_card():
    from app.kanban import mcp_server as m

    cid = (await m.create_card("MCP", "wip", ""))["id"]
    res = await m.request_review(cid, "too early")
    assert res["error"] == "not_in_done"
    assert res["column"] == "Backlog"
