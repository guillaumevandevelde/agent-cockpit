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


# --- report_impediment with structured options (gate-style) ---
# Acceptance criterion: `report_impediment` accepts an optional
# `options: list[str]`. When supplied, a KanbanGate row is created in addition
# to the existing comment + release + move-to-Impediment sequence. The card's
# activity feed gets the `**Impediment:** <question>` comment (matching the
# existing extraction logic in dispatch.py + router.resolve_impediment) and
# the gate carries the candidate options + status="open". The card is
# released so the session ends — no blocking poll. See report_impediment in
# mcp_server.py and the implementation of /cards/{cid}/resolve-impediment in
# router.py for how the chosen option threads back into the resumed prompt.


@pytest.mark.asyncio
async def test_report_impediment_with_options_creates_open_gate():
    """options= must materialize a KanbanGate row with status='open' so the UI
    can render choice buttons on the card in the Impediment column (mirrors
    the open_gate path)."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanGate

    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    await m.report_impediment(
        cid,
        "Postgres or SQLite?",
        options=["Postgres", "SQLite", "Doesn't matter — pick one"],
    )

    async with KanbanSessionLocal() as s:
        gates = (await s.execute(
            __import__("sqlalchemy").select(KanbanGate)
            .where(KanbanGate.card_id == cid)
        )).scalars().all()

    assert len(gates) == 1
    gate = gates[0]
    assert gate.question == "Postgres or SQLite?"
    assert gate.options == ["Postgres", "SQLite", "Doesn't matter — pick one"]
    assert gate.status == "open"
    assert gate.answer is None


@pytest.mark.asyncio
async def test_report_impediment_with_options_releases_claim():
    """options= must NOT change the existing release-on-impediment semantics —
    the calling session ends immediately so the worktree can be GC'd. Verifies
    the 'sessie sluit, blokkeert niet' acceptance criterion."""
    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    result = await m.report_impediment(
        cid, "Pick A or B", options=["A", "B"],
    )
    assert result["claimed_by"] is None
    assert result["column"] == "Impediment"


@pytest.mark.asyncio
async def test_report_impediment_without_options_still_works():
    """Backwards compat: omitting options keeps the legacy free-text path —
    no KanbanGate is created, no exceptions, comment + move + release only.
    Mirrors the existing call site in engineer.md / analyst.md that pass
    only `question`."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanGate

    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    result = await m.report_impediment(cid, "Need a human, please answer in chat.")

    assert result["claimed_by"] is None
    assert result["column"] == "Impediment"

    async with KanbanSessionLocal() as s:
        gates = (await s.execute(
            __import__("sqlalchemy").select(KanbanGate)
            .where(KanbanGate.card_id == cid)
        )).scalars().all()
    assert gates == []


@pytest.mark.asyncio
async def test_report_impediment_with_options_posts_impediment_comment():
    """The `**Impediment:** <question>` comment must still be posted when
    options= is supplied — the same prefix dispatch.extract_revisit_question
    and router.resolve_impediment walk to find the question. Otherwise the
    resume prompt would lose the question text the gate doesn't surface on
    its own."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import card_activity

    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    await m.report_impediment(
        cid, "Postgres or SQLite?", options=["Postgres", "SQLite"],
    )

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, cid)
    comment_ops = [o for o in ops if o.op_type == "comment"]
    assert any("**Impediment:** Postgres or SQLite?" in o.payload["text"]
               for o in comment_ops)


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


# --- work_type auto-fill on create_card -------------------------------------
# Regression: the REST create_card path applies resolve_create_agent to auto-fill
# card.agent from work_type (commit 80e139e). The MCP create_card tool didn't,
# so MCP-created cards ended up with agent=None and — when work_type was
# 'analysis' — the dispatcher routed them to 'engineer' (the hardcoded
# fallback in _phase_target_agent). This regressed kanban card 9cf106e7
# ("Card with analysis work type got picked up by an engineer"). The fix is
# for MCP create_card to accept work_type and apply the same auto-fill.


@pytest.mark.asyncio
async def test_mcp_create_card_accepts_work_type_and_auto_fills_agent():
    """work_type='analysis' + no explicit agent → card.agent == 'analyst'."""
    card = await m.create_card("P", "Investigate X", "", "Backlog", "analysis")
    assert card["work_type"] == "analysis"
    assert card["agent"] == "analyst", (
        "MCP create_card must apply resolve_create_agent so work_type='analysis' "
        "auto-fills agent='analyst' (mirrors the REST path post-80e139e). "
        "Otherwise the dispatcher routes it to engineer."
    )


@pytest.mark.asyncio
async def test_mcp_create_card_explicit_agent_overrides_work_type():
    """Explicit agent still wins, same as the REST contract."""
    card = await m.create_card(
        "P", "Force engineer", "", "Backlog", "analysis", "engineer",
    )
    assert card["work_type"] == "analysis"
    assert card["agent"] == "engineer"


@pytest.mark.asyncio
async def test_mcp_create_card_no_work_type_leaves_agent_empty():
    """No work_type, no agent → card.agent stays None (no mapping to apply)."""
    card = await m.create_card("P", "Plain card")
    assert card["agent"] is None
    assert card["work_type"] is None


# --- parent_card_id on create_card ------------------------------------------
# Regression: the analyst workflow is
#   create_card(child) × N → add_plan_attachment(parent, child_card_ids)
# and add_plan_attachment rejects any child whose parent_card_id != parent
# (mcp_server.py:472 returns {"error": "parent_mismatch"}). The REST
# CardCreate schema already accepts parent_card_id, but the MCP wrapper
# didn't expose it — analysts had to PATCH the card after creation as a
# workaround (see kanban card 3f8ccfab70f44672908a8b1559754148). The fix is
# to mirror the REST contract on the MCP tool.


@pytest.mark.asyncio
async def test_mcp_create_card_accepts_parent_card_id():
    """A parent_card_id passed at create time must round-trip through the
    create op-log and be visible on the resulting card — so a subsequent
    add_plan_attachment call sees parent_card_id == expected_parent instead
    of returning {"error": "parent_mismatch"}."""
    parent = await m.create_card("P", "Parent")
    child = await m.create_card("P", "Child", parent_card_id=parent["id"])
    assert child["parent_card_id"] == parent["id"]


@pytest.mark.asyncio
async def test_mcp_create_card_omitted_parent_card_id_stays_none():
    """Omitting parent_card_id must leave the column None (backwards compat)."""
    card = await m.create_card("P", "Standalone")
    assert card["parent_card_id"] is None


@pytest.mark.asyncio
async def test_mcp_create_card_then_add_plan_attachment_round_trip():
    """End-to-end: create parent + children via MCP, then bind them with
    add_plan_attachment. Pre-fix this returned {"error": "parent_mismatch"}
    because children were born without parent_card_id."""
    parent = await m.create_card("P", "Parent", work_type="analysis")
    child_a = await m.create_card("P", "Child A", parent_card_id=parent["id"])
    child_b = await m.create_card("P", "Child B", parent_card_id=parent["id"])

    result = await m.add_plan_attachment(
        parent["id"], "# Plan\n\nDo the thing.", [child_a["id"], child_b["id"]],
    )
    assert result["parent_card_id"] == parent["id"]
    assert result["child_card_ids"] == [child_a["id"], child_b["id"]]
