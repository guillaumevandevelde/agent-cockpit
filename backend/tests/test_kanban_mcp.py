# backend/tests/test_kanban_mcp.py
import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_create_then_list_then_claim():
    created = await m.create_card("P", "Do the thing", "details")
    cid = created["id"]
    listed = await m.list_cards("P")
    assert any(c["id"] == cid for c in listed)
    claimed = await m.claim_card(cid, "sess1@devA")
    assert claimed["claimed_by"] == "sess1@devA"


@pytest.mark.asyncio
async def test_claim_conflict_returns_error_dict():
    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "first@d")
    result = await m.claim_card(cid, "second@d")
    assert result["error"] == "already_claimed"
    assert result["owner"] == "first@d"


# --- null-safety: tools on non-existent cards return {"error": "not_found"} ---

@pytest.mark.asyncio
async def test_get_card_not_found():
    result = await m.get_card("nonexistent-id")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_move_card_not_found():
    result = await m.move_card("nonexistent-id", "Done")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_update_card_not_found():
    result = await m.update_card("nonexistent-id", title="new title")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_claim_card_not_found():
    result = await m.claim_card("nonexistent-id", "owner@d")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_release_card_not_found():
    result = await m.release_card("nonexistent-id")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_attach_deliverable_not_found():
    result = await m.attach_deliverable("nonexistent-id", "branch", "feature/x")
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_report_impediment_not_found():
    result = await m.report_impediment("nonexistent-id", "What should I do?")
    assert result.get("error") == "not_found"


# --- comment works even for non-existent card (pure log entry) ---

@pytest.mark.asyncio
async def test_comment_returns_ok_dict():
    cid = (await m.create_card("P", "t", ""))["id"]
    result = await m.comment(cid, "progress update")
    assert result.get("ok") is True


# --- ping ---

@pytest.mark.asyncio
async def test_ping_returns_ok():
    result = await m.ping()
    assert result.get("ok") is True
    assert "server" in result


# --- full move+attach+comment lifecycle ---

@pytest.mark.asyncio
async def test_full_lifecycle():
    card = await m.create_card("proj", "Build X", "desc")
    cid = card["id"]

    moved = await m.move_card(cid, "Done", summary="Built X and shipped it.")
    assert moved["column"] == "Done"

    attached = await m.attach_deliverable(cid, "branch", "main")
    assert any(d["ref"] == "main" for d in attached["deliverables"])

    comment_result = await m.comment(cid, "shipped!")
    assert comment_result["ok"] is True


# --- move_card requires a summary when landing on Done/Impediment ---

@pytest.mark.asyncio
async def test_move_card_to_done_without_summary_is_rejected():
    cid = (await m.create_card("P", "t", ""))["id"]
    result = await m.move_card(cid, "Done")
    assert result.get("error") == "summary_required"
    # card must stay put — the rejected move must not have applied
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_card_to_impediment_without_summary_is_rejected():
    cid = (await m.create_card("P", "t", ""))["id"]
    result = await m.move_card(cid, "Impediment")
    assert result.get("error") == "summary_required"
    card = await m.get_card(cid)
    assert card["column"] != "Impediment"


@pytest.mark.asyncio
async def test_move_card_to_done_with_blank_summary_is_rejected():
    cid = (await m.create_card("P", "t", ""))["id"]
    result = await m.move_card(cid, "Done", summary="   ")
    assert result.get("error") == "summary_required"


@pytest.mark.asyncio
async def test_move_card_to_done_with_summary_posts_it_as_a_comment():
    cid = (await m.create_card("P", "t", ""))["id"]
    moved = await m.move_card(cid, "Done", summary="Implemented the thing and tested it.")
    assert moved["column"] == "Done"

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import card_activity

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, cid)
    comment_ops = [o for o in ops if o.op_type == "comment"]
    assert len(comment_ops) == 1
    assert "Implemented the thing and tested it." in comment_ops[0].payload["text"]


@pytest.mark.asyncio
async def test_move_card_to_other_columns_does_not_require_summary():
    cid = (await m.create_card("P", "t", ""))["id"]
    moved = await m.move_card(cid, "Doing")
    assert moved["column"] == "Doing"
    moved = await m.move_card(cid, "To Resume")
    assert moved["column"] == "To Resume"


# --- resolve_project_key: MCP-only path to the real board key, so agents ---
# --- without shell/HTTP access don't have to guess a project string. -------

@pytest.mark.asyncio
async def test_resolve_project_key_returns_git_key(monkeypatch):
    monkeypatch.setattr(
        m, "_resolve_project_key",
        lambda path: "git:github.com/u/repo",
    )
    result = await m.resolve_project_key("/some/path")
    assert result == {"project_key": "git:github.com/u/repo"}


@pytest.mark.asyncio
async def test_resolve_project_key_matches_what_create_card_should_use(monkeypatch):
    """The key resolve_project_key returns is exactly what a subsequent
    create_card/list_cards call must use as `project` — proves the new tool
    actually closes the fragmentation gap instead of just returning a key
    the rest of the API ignores."""
    monkeypatch.setattr(
        m, "_resolve_project_key",
        lambda path: "git:github.com/u/repo",
    )
    resolved = await m.resolve_project_key("/some/path")
    cid = (await m.create_card(resolved["project_key"], "t", ""))["id"]
    listed = await m.list_cards(resolved["project_key"])
    assert any(c["id"] == cid for c in listed)
