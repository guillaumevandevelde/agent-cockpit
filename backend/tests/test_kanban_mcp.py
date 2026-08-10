# backend/tests/test_kanban_mcp.py
import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_create_then_list_then_claim():
    created = await m.create_card("P", "Do the thing", "details", confirm_new_project=True)
    cid = created["id"]
    listed = await m.list_cards("P")
    assert any(c["id"] == cid for c in listed)
    claimed = await m.claim_card(cid, "sess1@devA")
    assert claimed["claimed_by"] == "sess1@devA"


@pytest.mark.asyncio
async def test_list_cards_compact_returns_summary_shape_via_mcp():
    """MCP `list_cards(compact=True)` returns the dedupe-friendly shape
    (id, title, column, work_type, rank) and skips the per-card
    enrich_done_info / impediment_status_for_card op-log walks so a 27+
    card Backlog no longer overflows the MCP token cap.

    The default path must stay untouched (existing agents depend on the
    full CardResponse-shaped dicts)."""
    import json as _json

    fat = "long description body " * 50
    cid = (await m.create_card("MCP-COMPACT", "Compact MCP card", fat,
                                work_type="bug", confirm_new_project=True))["id"]

    default = await m.list_cards("MCP-COMPACT")
    assert len(default) == 1
    assert "description" in default[0]
    assert "deliverables" in default[0]
    assert "done_summary" in default[0]
    default_size = len(_json.dumps(default))

    compact = await m.list_cards("MCP-COMPACT", compact=True)
    assert len(compact) == 1
    assert set(compact[0].keys()) == {"id", "title", "column",
                                       "work_type", "rank"}, (
        f"MCP compact shape drifted: keys={sorted(compact[0].keys())}"
    )
    assert compact[0]["id"] == cid
    assert compact[0]["title"] == "Compact MCP card"
    assert compact[0]["column"] == "Backlog"
    assert compact[0]["work_type"] == "bug"

    compact_size = len(_json.dumps(compact))
    # Compact must be substantially smaller than default.
    assert compact_size * 5 < default_size, (
        f"MCP compact ({compact_size}B) not substantially smaller than "
        f"default ({default_size}B)"
    )


@pytest.mark.asyncio
async def test_list_cards_compact_default_is_false_backwards_compatible_mcp():
    """Calling m.list_cards(project) without compact must still return the
    full CardResponse-shaped dict (description present, etc.)."""
    await m.create_card("MCP-BC", "T", "description here", confirm_new_project=True)
    listed = await m.list_cards("MCP-BC")
    assert len(listed) == 1
    assert listed[0]["description"] == "description here"
    assert "deliverables" in listed[0]


@pytest.mark.asyncio
async def test_claim_conflict_returns_error_dict():
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    await m.claim_card(cid, "first@d")
    result = await m.claim_card(cid, "second@d")
    assert result["error"] == "already_claimed"
    assert result["owner"] == "first@d"


# --- null-safety: tools on non-existent cards return {"error": "not_found"} ---

@pytest.mark.asyncio
async def test_get_card_not_found():
    result = await m.get_card("nonexistent-id")
    assert result.get("error") == "not_found"


# --- get_card prefix-match support (kaart [self-improve] 068845bd…) ---
# Every human/agent-written reference to a card uses the shortened form
# (`068845bd…`), but `get_card` only accepted full 32-char ids. The fix:
# accept a unique prefix of ≥8 chars, return `ambiguous_card_id` for
# ≥2 matches, and reject prefixes shorter than 8 chars. Exact 32-char
# ids keep working unchanged.


@pytest.mark.asyncio
async def test_get_card_full_id_still_works():
    """Regression: a full 32-char hex id must still resolve to the card
    via the exact-match path — every existing caller passes full ids."""
    full_id = "0" * 32  # 32-char hex, deterministic, easy to type
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="FULL", entity_id=full_id, payload={"title": "exact-id"})
        await s.commit()
    result = await m.get_card(full_id)
    assert result.get("error") is None
    assert result["id"] == full_id
    assert result["title"] == "exact-id"


@pytest.mark.asyncio
async def test_get_card_unique_prefix_returns_card():
    """A unique prefix of ≥8 chars resolves to the matching card."""
    full_id = "abcdef01" + "0" * 24  # 32 chars, starts with "abcdef01"
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="PFX", entity_id=full_id, payload={"title": "alpha"})
        await s.commit()
    # Sanity: the exact id still works
    full_result = await m.get_card(full_id)
    assert full_result.get("error") is None
    assert full_result["id"] == full_id
    # The prefix lookup: 8 chars → resolves uniquely
    prefix_result = await m.get_card("abcdef01")
    assert prefix_result.get("error") is None, (
        f"unique prefix returned error: {prefix_result}"
    )
    assert prefix_result["id"] == full_id
    assert prefix_result["title"] == "alpha"


@pytest.mark.asyncio
async def test_get_card_ambiguous_prefix_returns_matches():
    """A prefix that matches ≥2 cards returns `ambiguous_card_id` with
    the full ids — never silently picks the first match."""
    shared = "deadbeef"
    full_a = shared + "0" * (32 - len(shared))      # "deadbeef000…000"
    full_b = shared + "1" * (32 - len(shared))      # "deadbeef111…111"
    assert full_a != full_b
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="AMB", entity_id=full_a, payload={"title": "first"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="AMB", entity_id=full_b, payload={"title": "second"})
        await s.commit()
    result = await m.get_card(shared)  # 8-char prefix matches both
    assert result.get("error") == "ambiguous_card_id", (
        f"expected ambiguous_card_id, got: {result}"
    )
    matches = result.get("matches")
    # Acceptance criterion 3: assert on the *content* of `matches`,
    # not just on the presence of an error key — a silently-empty
    # `matches` list would still pass `result["error"] == ...`.
    assert isinstance(matches, list), (
        f"matches must be a list of full ids, got {type(matches).__name__}: {matches!r}"
    )
    assert sorted(matches) == sorted([full_a, full_b]), (
        f"matches must contain both full ids, got: {matches}"
    )


@pytest.mark.asyncio
async def test_get_card_short_prefix_rejected():
    """A prefix shorter than 8 chars is rejected — too many collisions
    possible below that threshold (acceptance criterion 1)."""
    # 7 chars — below the floor
    result = await m.get_card("abcdef0")
    assert result.get("error") == "prefix_too_short"
    assert result.get("min_length") == 8


@pytest.mark.asyncio
async def test_get_card_unknown_prefix_returns_not_found_with_hint():
    """A unique-but-nonexistent prefix returns `not_found` *with a hint*
    that prefixes are allowed, so the caller doesn't doubt the reference
    instead of the id-form."""
    result = await m.get_card("01234567")  # 8-char valid-length prefix, no card
    assert result.get("error") == "not_found"
    # Hint should mention prefix so the operator doesn't loop on "this id
    # doesn't exist" — the id-form is fine, the lookup just didn't hit.
    assert "prefix" in str(result.get("message", "")).lower()


# --- end get_card prefix-match tests ---


@pytest.mark.asyncio
async def test_get_card_returns_project_key_unchanged_for_round_trip_with_list_cards():
    """Regression for kanban card b99d03c363994b4bb7d78c26e82647e0.

    `get_card` must hand back exactly the `project_key` stored in
    `kanban_cards` — no pre-rebrand alias rewrite on the read path — so a
    caller can pipe that value straight into `list_cards` / `create_card`
    without a detour through `resolve_project_key`. The historical failure
    mode: get_card returned the pre-rebrand key while the DB row already
    carried the current key, so list_cards(die_key) refused with
    `unknown_project_key` and every agent-flow that did get_card → list_cards
    paid one extra call plus the resolve detour."""
    project_key = "git:github.com/guillaumevandevelde/agent-cockpit"
    full_id = "f" * 32
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=full_id,
            payload={"title": "round-trip"})
        await s.commit()

    got = await m.get_card(full_id)
    assert got.get("error") is None, f"get_card failed: {got}"
    assert got["project_key"] == project_key, (
        f"get_card rewrote project_key: stored={project_key!r} "
        f"returned={got.get('project_key')!r}"
    )

    # The key from get_card must work as input to list_cards without any
    # alias mapping or resolve_project_key detour — that round-trip is the
    # whole point of acceptance criterion 2.
    listed = await m.list_cards(project_key)
    assert any(c["id"] == full_id for c in listed), (
        f"project_key from get_card ({got['project_key']!r}) did not round-trip "
        f"through list_cards: got {len(listed)} rows, none matching {full_id}"
    )


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

    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    await m.report_impediment(
        cid,
        "Postgres or SQLite?",
        options=["Postgres", "SQLite", "MySQL", "Doesn't matter — pick one"],
    )

    async with KanbanSessionLocal() as s:
        gates = (await s.execute(
            __import__("sqlalchemy").select(KanbanGate)
            .where(KanbanGate.card_id == cid)
        )).scalars().all()

    assert len(gates) == 1
    gate = gates[0]
    assert gate.question == "Postgres or SQLite?"
    assert gate.options == ["Postgres", "SQLite", "MySQL", "Doesn't matter — pick one"]
    assert gate.status == "open"
    assert gate.answer is None


@pytest.mark.asyncio
async def test_report_impediment_with_options_releases_claim():
    """options= must NOT change the existing release-on-impediment semantics —
    the calling session ends immediately so the worktree can be GC'd. Verifies
    the 'sessie sluit, blokkeert niet' acceptance criterion."""
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    result = await m.report_impediment(
        cid, "Pick A or B", options=["A", "B", "C", "D"],
    )
    assert result["claimed_by"] is None
    assert result["column"] == "Impediment"


@pytest.mark.asyncio
async def test_report_impediment_rejects_non_four_option_count():
    """Kaart 4279448c revisit: the Impediment UI must always show 4
    agent-proposed buttons, never a UI-injected filler padding a shorter
    list. The enforcement point is here — `options` must be exactly 4 when
    supplied, or the call is rejected with no side effects (no move, no
    comment, no gate, no release)."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanGate
    from app.kanban.service import card_activity

    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    await m.claim_card(cid, "agent:sess1@devA")

    for bad_options in (["A"], ["A", "B"], ["A", "B", "C"],
                        ["A", "B", "C", "D", "E"]):
        result = await m.report_impediment(cid, "Pick one", options=bad_options)
        assert result.get("error") == "invalid_option_count"

    async with KanbanSessionLocal() as s:
        card = await m.service.get_card(s, cid)
        gates = (await s.execute(
            __import__("sqlalchemy").select(KanbanGate)
            .where(KanbanGate.card_id == cid)
        )).scalars().all()
    assert card.column != "Impediment"
    assert card.claimed_by is not None
    assert gates == []
    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, cid)
    assert not any("**Impediment:**" in (o.payload or {}).get("text", "")
                   for o in ops if o.op_type == "comment")


@pytest.mark.asyncio
async def test_report_impediment_without_options_still_works():
    """Backwards compat: omitting options keeps the legacy free-text path —
    no KanbanGate is created, no exceptions, comment + move + release only.
    Mirrors the existing call site in engineer.md / analyst.md that pass
    only `question`."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanGate

    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
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

    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    await m.claim_card(cid, "agent:sess1@devA")
    await m.report_impediment(
        cid, "Postgres or SQLite?",
        options=["Postgres", "SQLite", "MySQL", "MariaDB"],
    )

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, cid)
    comment_ops = [o for o in ops if o.op_type == "comment"]
    assert any("**Impediment:** Postgres or SQLite?" in o.payload["text"]
               for o in comment_ops)


# --- comment works even for non-existent card (pure log entry) ---

@pytest.mark.asyncio
async def test_comment_returns_ok_dict():
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
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
    card = await m.create_card("proj", "Build X", "desc", confirm_new_project=True)
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
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done")
    assert result.get("error") == "summary_required"
    # card must stay put — the rejected move must not have applied
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_card_to_impediment_without_summary_is_rejected():
    """Reversed contract: move_card(Impediment) is rejected with
    `use_report_impediment`, *not* `summary_required`, because the gate
    fires before the summary check (kaart b8e3ac8b… decision A). The
    takeaway is the same — the card never lands in Impediment — but the
    error code steers the agent straight at report_impediment, which is
    the only route that opens a KanbanGate and renders the 4-button
    picker instead of an empty screen."""
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Impediment")
    assert result.get("error") == "use_report_impediment"
    card = await m.get_card(cid)
    assert card["column"] != "Impediment"


@pytest.mark.asyncio
async def test_move_card_to_impediment_with_summary_is_still_rejected():
    """The new gate fires regardless of `summary` — even a perfectly
    written summary on `move_card(column="Impediment")` is refused,
    because the issue is the missing gate, not the missing prose. The
    dedicated error message must point at report_impediment by name."""
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Impediment", summary="I am stuck on a merge.")
    assert result.get("error") == "use_report_impediment"
    msg = result.get("message", "")
    assert "report_impediment" in msg, (
        f"message must name the tool to use instead, got: {msg!r}"
    )
    card = await m.get_card(cid)
    assert card["column"] != "Impediment"


@pytest.mark.asyncio
async def test_move_card_to_done_with_blank_summary_is_rejected():
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="   ")
    assert result.get("error") == "summary_required"


@pytest.mark.asyncio
async def test_move_card_to_done_with_summary_posts_it_as_a_comment():
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
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
    cid = (await m.create_card("P", "t", "", confirm_new_project=True))["id"]
    moved = await m.move_card(cid, "Doing")
    assert moved["column"] == "Doing"
    moved = await m.move_card(cid, "To Resume")
    assert moved["column"] == "To Resume"


# --- move_card outcome gate (analysis-outcome-contract-decision §5) -------
#
# Analysis cards (`work_type='analysis'` or `agent='analyst'`) moving to
# Done must carry an explicit `outcome` from the closed enum
# `{decomposed, not_feasible, no_action_needed}`. `decomposed` is verified
# against real child cards; `not_feasible` / `no_action_needed` set a
# label and post a `**Outcome:** …` activity-feed comment. Non-analysis
# cards are unaffected (backwards compatible). See docs/cockpit/kanban-
# conventions.md §2 for the comment-prefix contract.

@pytest.mark.asyncio
async def test_move_analysis_card_to_done_without_outcome_is_rejected():
    """An analysis card (work_type='analysis') without outcome is refused.

    Mirrors the summary_required pattern: card stays put, an actionable
    error listing the three allowed values comes back."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="analysis done")
    assert result.get("error") == "outcome_required"
    # three allowed values should be mentioned in the message body
    msg = result.get("message", "")
    for value in ("decomposed", "not_feasible", "no_action_needed"):
        assert value in msg, f"{value} missing from message: {msg!r}"
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analyst_agent_card_to_done_without_outcome_is_rejected():
    """Routing via agent='analyst' (legacy/manual override) is also gated.

    `is_analyst_leaf_spike` checks both `work_type == 'analysis'` and
    `agent == 'analyst'` — this confirms the agent-attribute path is also
    subject to the gate."""
    cid = (await m.create_card("P", "analyse", "", agent="analyst", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="analysis done")
    assert result.get("error") == "outcome_required"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_with_invalid_outcome_is_rejected():
    """Unknown outcome values fail closed with the allowed-set echoed back.

    The three-value check predates analysis-outcome-contract-decision.md §9;
    §9 adds `filed_standalone` for cadence triggers whose findings are filed
    standalone. Companion assertion below (`…_with_invalid_outcome_lists_four_allowed`)
    pins the four-value set explicitly; this one stays backwards-compatible
    with a subset check (the old three are a subset of the new four)."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="analysis done",
                                outcome="finished")
    assert result.get("error") == "invalid_outcome"
    # Subset check: the three legacy enums must remain in `allowed` (nothing
    # got renamed); the full-set counterpart lives in the next test.
    assert {"decomposed", "not_feasible", "no_action_needed"} <= set(result.get("allowed", []))
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_without_children_is_rejected():
    """`decomposed` is verified, not trusted: without ≥1 child card the
    move is refused — this is the anti-lie check."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="split into subtasks",
                                outcome="decomposed")
    assert result.get("error") == "no_children"
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_with_children_is_allowed():
    """`decomposed` with ≥1 child is the happy path — the children are the
    proof of work, no extra label is set (the children are the artefact).

    The parent lands in `Awaiting Subtasks`, not `Done` — it parks until
    every child reaches Done (decision doc §3/§6: `decomposed` is by
    definition "has children", which is exactly the parking condition)."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    # Create a child card pointing back at the parent.
    child = await m.create_card("P", "child", "",
                         parent_card_id=parent, confirm_new_project=True)
    # Bind the plan_ref via add_plan_attachment so the decomposed move
    # passes the missing-plan_ref gate (kaart 2341a40e…); the gate's own
    # coverage lives in the test that follows this one.
    await m.add_plan_attachment(
        parent, "# Plan\n\nDo the thing.", [child["id"]],
    )
    result = await m.move_card(parent, "Done",
                                summary="split into subtasks",
                                outcome="decomposed")
    assert result["column"] == "Awaiting Subtasks"
    # No label is set for `decomposed`; children themselves are the proof.
    assert "not-feasible" not in (result.get("labels") or [])
    assert "no-action-needed" not in (result.get("labels") or [])
    # An Outcome comment is posted with the summary verbatim.
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import card_activity

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, parent)
    outcome_comments = [
        o for o in ops
        if o.op_type == "comment"
        and "**Outcome:** decomposed — split into subtasks" in (o.payload.get("text") or "")
    ]
    assert len(outcome_comments) == 1


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_without_plan_ref_is_rejected():
    """The step-3-without-step-4 silent no-op (kaart 2341a40e…): child
    cards created but no plan_ref attached by add_plan_attachment.
    Refusing the parent-move is the gate that prevents the parked
    children from ever existing in the first place."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    # Create TWO children — neither has a plan_ref, so the gate must
    # catch all of them, not just the first one.
    child1 = (await m.create_card("P", "child1", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    child2 = (await m.create_card("P", "child2", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    result = await m.move_card(parent, "Done",
                                summary="split into subtasks",
                                outcome="decomposed")
    assert result.get("error") == "missing_plan_ref", result
    # Both offending child ids must be named in the error message —
    # the operator needs to know which children to bind, not just that
    # the gate fired.
    assert child1 in result.get("message", "")
    assert child2 in result.get("message", "")
    card = await m.get_card(parent)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_not_feasible_sets_label_and_comment():
    """`not_feasible` appends the canonical `not-feasible` label (preserving
    any pre-existing labels) and posts the **Outcome:** comment.

    Verifies the append-not-overwrite invariant: we pre-set a label on the
    card, call move_card with outcome=not_feasible, and assert both labels
    are present after the move."""
    cid = (await m.create_card("P", "analyse", "",
                                work_type="analysis", confirm_new_project=True))["id"]
    # Seed a pre-existing label via the op-log (the MCP create_card wrapper
    # doesn't surface `labels` — sibling chore on the kanban board fills
    # that gap; for the gate itself we only need to prove append-not-
    # overwrite, which is easier to express by seeding via the same op-
    # log path the gate itself uses under the hood).
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload={"labels": ["pre-existing"]})
        await s.commit()
    result = await m.move_card(cid, "Done",
                                summary="scope too broad — punt on this",
                                outcome="not_feasible")
    assert result["column"] == "Done"
    assert "not-feasible" in (result.get("labels") or [])
    # pre-existing label survives the merge
    assert "pre-existing" in (result.get("labels") or [])

    from app.kanban.service import card_activity

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, cid)
    outcome_comments = [
        o for o in ops
        if o.op_type == "comment"
        and "**Outcome:** not_feasible — scope too broad — punt on this" in (o.payload.get("text") or "")
    ]
    assert len(outcome_comments) == 1


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_no_action_needed_sets_label_and_comment():
    """`no_action_needed` is the symmetric counterpart of `not_feasible`:
    label `no-action-needed`, append-not-overwrite, **Outcome:** comment."""
    cid = (await m.create_card("P", "analyse", "",
                                work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="decision-doc only, no follow-up",
                                outcome="no_action_needed")
    assert result["column"] == "Done"
    assert "no-action-needed" in (result.get("labels") or [])


@pytest.mark.asyncio
async def test_move_non_analysis_card_to_done_ignores_outcome():
    """Backwards compatibility: feature/bug/chore cards (or untyped ones)
    with work_type != 'analysis' and agent != 'analyst' keep accepting a
    bare summary=... and outcome, if supplied, is recorded as a normal
    op but never gates anything.

    This proves the gate is limited to the predicate — engineers, plan
    creators, and bystander cards all stay on the legacy path."""
    cid = (await m.create_card("P", "feature", "", work_type="feature", confirm_new_project=True))["id"]
    # No outcome: legacy path.
    moved = await m.move_card(cid, "Done", summary="shipped the feature")
    assert moved["column"] == "Done"


@pytest.mark.asyncio
async def test_move_card_to_other_columns_ignores_outcome():
    """The outcome gate only fires on Done. A Backlog→Doing move with a
    bogus outcome string is untouched — the column isn't Done, the gate
    isn't triggered, and there's no spurious rejection."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    moved = await m.move_card(cid, "Doing", outcome="decomposed")
    assert moved["column"] == "Doing"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_with_invalid_outcome_lists_four_allowed_legacy():
    """DEPRECATED (kaart 85f231f0…). Kept as a subset-check pin so any
    future change that REMOVES one of the original four values fails
    loud — the new test
    `test_move_analysis_card_to_done_with_invalid_outcome_lists_five_allowed`
    supersedes this for the full closed-enum contract. The subset check
    predates the §10 expansion."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="x",
                                outcome="not_a_real_outcome")
    assert result.get("error") == "invalid_outcome"
    assert {"decomposed", "not_feasible", "no_action_needed", "filed_standalone"} <= set(result.get("allowed", []))


# --- filed_standalone (analysis-outcome-contract-decision.md §9) ---
#
# Companion of the three enum values for cadence triggers whose findings
# deliberately carry no parent_card_id to the trigger
# (`recurring-cadence-proposal.md` §4.3). Verification reads
# `card.metadata.filed_card_ids`, requires ≥1 entry, and confirms every
# id resolves to a real card in the same project_key.

@pytest.mark.asyncio
async def test_move_analysis_card_to_done_filed_standalone_without_metadata_is_rejected():
    """`filed_standalone` without `metadata.filed_card_ids` is refused —
    same shape as `no_children` for `decomposed`, same UX
    (analysis-outcome-contract-decision.md §9). Without this gate, an
    agent could silently mislabel a `no-op` run as productive; that is
    exactly the failure mode the decision doc describes
    (`no_action_needed` would let a productive run lie)."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="filed 3 cards this week",
                                outcome="filed_standalone")
    assert result.get("error") == "no_filed_cards"
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_filed_standalone_with_empty_list_is_rejected():
    """An empty `filed_card_ids` list passes the type check (it's a list)
    but fails the cardinality check — refuse, don't pretend."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload={"metadata": {"filed_card_ids": []}})
        await s.commit()
    result = await m.move_card(cid, "Done",
                                summary="empty filed list",
                                outcome="filed_standalone")
    assert result.get("error") == "no_filed_cards"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_filed_standalone_with_unknown_ids_is_rejected():
    """Typo'd ids or foreign-project ids must not satisfy the witness —
    same posture as `decomposed`'s `parent_card_id == card.id` check, but
    scoped via `card.metadata` instead of a FK."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload={"metadata": {"filed_card_ids": ["made-up-id-xyz"]}})
        await s.commit()
    result = await m.move_card(cid, "Done",
                                summary="typo'd id",
                                outcome="filed_standalone")
    assert result.get("error") == "no_filed_cards"
    # The error message lists the missing ids so the agent can self-correct.
    assert "made-up-id-xyz" in result.get("message", "")


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_filed_standalone_with_real_ids_succeeds():
    """The happy path: trigger card filed N real cards in the same
    project, no parent_card_id (that's the whole point of the new
    outcome — survives the trigger without polluting Awaiting-Subtasks
    parking). Lands in Done with `**Outcome:**` comment; no extra label
    is set (decision §9: it's a card-relationship outcome, not an
    outcome taxonomy)."""
    trigger = (await m.create_card("P", "trigger",
                                    work_type="analysis",
                                    confirm_new_project=True))["id"]
    filed_a = (await m.create_card("P", "finding-a", ""))["id"]
    filed_b = (await m.create_card("P", "finding-b", ""))["id"]
    # Note: NO `parent_card_id` — the filed cards deliberately don't
    # back-link to the trigger (recurring-cadence-proposal §4.3).
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=trigger,
            payload={"metadata": {"filed_card_ids": [filed_a, filed_b]}})
        await s.commit()
    result = await m.move_card(trigger, "Done",
                                summary="filed 2 standalone findings",
                                outcome="filed_standalone")
    assert result["column"] == "Done"
    # No labels should be appended — that's the §9 design.
    labels = result.get("labels") or []
    assert "filed-standalone" not in labels
    assert "not-feasible" not in labels
    assert "no-action-needed" not in labels
    # Outcome comment lands verbatim.
    from app.kanban.service import card_activity
    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, trigger)
    outcome_comments = [
        o for o in ops
        if o.op_type == "comment"
        and "**Outcome:** filed_standalone — filed 2 standalone findings"
            in (o.payload.get("text") or "")
    ]
    assert len(outcome_comments) == 1


@pytest.mark.asyncio
async def test_outcome_required_message_mentions_filed_standalone_legacy():
    """DEPRECATED (kaart 85f231f0…). The new
    `test_outcome_required_message_mentions_decomposed_then_swept`
    supersedes this with the full five-value contract. Kept as a
    subset pin: any future change that DROPS one of the original four
    fails loud."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="analysis done")
    assert result.get("error") == "outcome_required"
    msg = result.get("message", "")
    for value in ("decomposed", "not_feasible", "no_action_needed",
                  "filed_standalone"):
        assert value in msg, f"{value} missing from message: {msg!r}"


# --- decomposed_then_swept (analysis-outcome-contract-decision §10) -------
#
# Fifth outcome: the analysis decomposed into children that have since
# been swept from the board (Clear Done, single-card delete). At move
# time there are zero live children, so `decomposed` refuses with
# `no_children`, and `no_action_needed` would be a lie. The kanban_ops
# op-log preserves the historical `create` events for those swept
# children (`payload.parent_card_id`), so the gate can verify ≥1 such
# event against `card.id`. Same anti-lie posture as `decomposed`, but
# the witness lives in the op-log, not in the live `kanban_cards` table.
# No extra label (mirrors `filed_standalone` §9: outcome-taxonomy vs.
# card-relationship distinction).

@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_then_swept_without_historical_children_is_rejected():
    """`decomposed_then_swept` is verified against the op-log: an analysis
    card that never had any children at all is refused. Mirrors the
    anti-lie check that `decomposed` runs against live children. This is
    what stops an honest-looking analysis from sneaking through the new
    outcome with no decomposition evidence whatsoever."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="claimed children but none exist",
                                outcome="decomposed_then_swept")
    assert result.get("error") == "no_historical_children", result
    card = await m.get_card(cid)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_then_swept_with_historical_children_succeeds():
    """Happy path: the analysis had children that are now swept. We
    reproduce that state by creating a child, then deleting it via the
    op-log (the test-DB reset wipes `kanban_cards` between tests, but
    `kanban_ops` is preserved long enough within a single test for the
    gate's verification to find the historical `create` event).

    Lands in Done (not Awaiting Subtasks — there are no live children
    to wait for). `**Outcome:**` comment lands verbatim; no extra
    label is set (mirrors §9 `filed_standalone` discipline)."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    # Create + delete a child via the op-log so the parent's historical
    # `payload.parent_card_id` witness is preserved after the row is gone.
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation

    async with KanbanSessionLocal() as s:
        child_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="P", entity_id=None,
            payload={"title": "swept child", "description": "",
                      "column": "Backlog", "work_type": None,
                      "agent": None, "parent_card_id": parent,
                      "depends_on": None, "labels": None, "metadata": None},
        )
        # Now delete it — the create op is preserved in kanban_ops.
        child = await s.get(KanbanCard, child_id)
        await s.delete(child)
        await s.commit()

    result = await m.move_card(parent, "Done",
                                summary="children finished and were swept",
                                outcome="decomposed_then_swept")
    assert result["column"] == "Done", result
    labels = result.get("labels") or []
    # No label is set — mirrors §9 (card-relationship outcome, not a
    # taxonomy value).
    assert "decomposed-then-swept" not in labels
    assert "not-feasible" not in labels
    assert "no-action-needed" not in labels

    from app.kanban.service import card_activity
    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, parent)
    outcome_comments = [
        o for o in ops
        if o.op_type == "comment"
        and "**Outcome:** decomposed_then_swept — children finished and were swept"
            in (o.payload.get("text") or "")
    ]
    assert len(outcome_comments) == 1


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_decomposed_then_swept_with_live_children_is_rejected():
    """If the analysis still has ≥1 live child, the operator is supposed
    to use `decomposed` (parent-park in Awaiting Subtasks) instead —
    `decomposed_then_swept` is only honest when zero live children
    remain. Without this gate, a parent in flight could short-circuit
    to Done and abandon its live children.

    Implementation choice: rather than refuse at the gate, we could
    silently redirect to `decomposed`-style parking, but that hides the
    state mismatch from the operator — refuse and let them pick the
    correct outcome."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    # Live child: parent_card_id back-link is current, no delete.
    child = (await m.create_card("P", "child", "",
                                  parent_card_id=parent, confirm_new_project=True))["id"]
    result = await m.move_card(parent, "Done",
                                summary="children still live, can't claim swept",
                                outcome="decomposed_then_swept")
    assert result.get("error") == "live_children_still_present", result
    msg = result.get("message", "")
    # The operator needs the child id to act on this.
    assert child in msg
    card = await m.get_card(parent)
    assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_move_analysis_card_to_done_with_invalid_outcome_lists_five_allowed():
    """After §10 the closed enum has FIVE values. A bogus outcome echoes
    the full allowed set so the gate's wire shape matches the contract —
    the same posture as the §9 four-value counterpart."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done",
                                summary="x",
                                outcome="not_a_real_outcome")
    assert result.get("error") == "invalid_outcome"
    assert set(result.get("allowed", [])) == {
        "decomposed", "not_feasible", "no_action_needed",
        "filed_standalone", "decomposed_then_swept",
    }


@pytest.mark.asyncio
async def test_outcome_required_message_mentions_decomposed_then_swept():
    """A missing outcome on an analysis-Done-move must list all five
    allowed enums (including the new one), so the gate's error message
    stays a contract, not a stale wish-list (§1)."""
    cid = (await m.create_card("P", "analyse", "", work_type="analysis", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="analysis done")
    assert result.get("error") == "outcome_required"
    msg = result.get("message", "")
    for value in ("decomposed", "not_feasible", "no_action_needed",
                  "filed_standalone", "decomposed_then_swept"):
        assert value in msg, f"{value} missing from message: {msg!r}"


# --- Awaiting Subtasks parent-parking (analyse-levenscyclus-decision §3) --
#
# Parent-generic: the condition is "has ≥1 child card", not work_type ==
# 'analysis'. Shares the interception point with the outcome gate above.

@pytest.mark.asyncio
async def test_move_card_with_children_parks_in_awaiting_subtasks():
    """Any card (not just analysis) with ≥1 child parks instead of
    reaching Done — the parent-generic rule from §3.1."""
    parent = (await m.create_card("P", "feature", "",
                                   work_type="feature", confirm_new_project=True))["id"]
    await m.create_card("P", "child", "", parent_card_id=parent, confirm_new_project=True)
    result = await m.move_card(parent, "Done", summary="shipped the parent piece")
    assert result["column"] == "Awaiting Subtasks"


@pytest.mark.asyncio
async def test_move_card_without_children_goes_directly_to_done():
    """Zero children is the majority case — nothing to park for, so the
    move lands in Done as before."""
    cid = (await m.create_card("P", "feature", "",
                                work_type="feature", confirm_new_project=True))["id"]
    result = await m.move_card(cid, "Done", summary="shipped it")
    assert result["column"] == "Done"


@pytest.mark.asyncio
async def test_parent_auto_closes_when_last_child_reaches_done():
    """Once every sibling is Done, the parent moves itself from Awaiting
    Subtasks to Done with a **Summary:** comment (§3.2) — no separate
    move_card call on the parent is needed."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    child1 = (await m.create_card("P", "child1", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    child2 = (await m.create_card("P", "child2", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    # Bind the plan_refs so the decomposed move passes the
    # missing-plan_ref gate (kaart 2341a40e…).
    await m.add_plan_attachment(
        parent, "# Plan\n\nDo the thing.", [child1, child2],
    )
    await m.move_card(parent, "Done", summary="split into subtasks", outcome="decomposed")
    assert (await m.get_card(parent))["column"] == "Awaiting Subtasks"

    await m.move_card(child1, "Done", summary="child1 done")
    assert (await m.get_card(parent))["column"] == "Awaiting Subtasks", (
        "one sibling still pending — parent must stay parked"
    )

    await m.move_card(child2, "Done", summary="child2 done")
    assert (await m.get_card(parent))["column"] == "Done"

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import card_activity

    async with KanbanSessionLocal() as s:
        ops = await card_activity(s, parent)
    # The parent's own move_card(Done) already posted a plain "**Summary:**
    # split into subtasks" comment (label keyed on the *requested* column,
    # unaffected by the park redirect) — the auto-close posts a second,
    # distinctly-worded one, so match on its specific text.
    auto_close_comments = [
        o for o in ops
        if o.op_type == "comment"
        and "auto-closed from Awaiting Subtasks" in (o.payload.get("text") or "")
    ]
    assert len(auto_close_comments) == 1


@pytest.mark.asyncio
async def test_parent_stays_parked_while_one_child_impeded():
    """A sibling that lands in Impediment (not Done) keeps the parent
    parked forever, by design (§3.2) — a human has to intervene."""
    parent = (await m.create_card("P", "analyse", "",
                                   work_type="analysis", confirm_new_project=True))["id"]
    child1 = (await m.create_card("P", "child1", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    child2 = (await m.create_card("P", "child2", "",
                                   parent_card_id=parent, confirm_new_project=True))["id"]
    await m.add_plan_attachment(
        parent, "# Plan\n\nDo the thing.", [child1, child2],
    )
    await m.move_card(parent, "Done", summary="split into subtasks", outcome="decomposed")

    await m.move_card(child1, "Done", summary="child1 done")
    # `move_card(Impediment)` is gated (kaart b8e3ac8b… decision A); use
    # report_impediment's free-text path to set up the same end state.
    await m.report_impediment(child2, "stuck, needs a human")

    assert (await m.get_card(parent))["column"] == "Awaiting Subtasks"


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
    cid = (await m.create_card(resolved["project_key"], "t", "", confirm_new_project=True))["id"]
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
    card = await m.create_card("P", "Investigate X", "", "Backlog", "analysis", confirm_new_project=True)
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
    confirm_new_project=True)
    assert card["work_type"] == "analysis"
    assert card["agent"] == "engineer"


@pytest.mark.asyncio
async def test_mcp_create_card_no_work_type_leaves_agent_empty():
    """No work_type, no agent → card.agent stays None (no mapping to apply)."""
    card = await m.create_card("P", "Plain card", confirm_new_project=True)
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
    parent = await m.create_card("P", "Parent", confirm_new_project=True)
    child = await m.create_card("P", "Child", parent_card_id=parent["id"], confirm_new_project=True)
    assert child["parent_card_id"] == parent["id"]


@pytest.mark.asyncio
async def test_mcp_create_card_omitted_parent_card_id_stays_none():
    """Omitting parent_card_id must leave the column None (backwards compat)."""
    card = await m.create_card("P", "Standalone", confirm_new_project=True)
    assert card["parent_card_id"] is None


@pytest.mark.asyncio
async def test_mcp_create_card_then_add_plan_attachment_round_trip():
    """End-to-end: create parent + children via MCP, then bind them with
    add_plan_attachment. Pre-fix this returned {"error": "parent_mismatch"}
    because children were born without parent_card_id."""
    parent = await m.create_card("P", "Parent", work_type="analysis", confirm_new_project=True)
    child_a = await m.create_card("P", "Child A", parent_card_id=parent["id"], confirm_new_project=True)
    child_b = await m.create_card("P", "Child B", parent_card_id=parent["id"], confirm_new_project=True)

    result = await m.add_plan_attachment(
        parent["id"], "# Plan\n\nDo the thing.", [child_a["id"], child_b["id"]],
    )
    assert result["parent_card_id"] == parent["id"]
    assert result["child_card_ids"] == [child_a["id"], child_b["id"]]


# --- depends_on on create_card / update_card --------------------------------
# Regression: the REST CardCreate / CardUpdate schemas accept depends_on
# (schemas.py:147, :169), the router honours it on PATCH
# (api/v1/kanban/router.py:329-360 → apply_operation → _materialize), and
# add_plan_attachment wires sibling deps via depends_on_graph. The MCP
# create_card / update_card tools, however, didn't surface depends_on — so
# any session that needed to wire sibling-deps on cards it just created
# (or retroactive tracking after the plan-attachment flow ran) had to drop
# to REST PATCH. Surface the field on both tools so it round-trips through
# the create+update op-log the same way the REST path already does.


@pytest.mark.asyncio
async def test_mcp_create_card_accepts_depends_on_and_persists_it():
    """A `depends_on=[...]` passed at create time must round-trip through the
    create op-log and be visible on the resulting card — so the dispatcher
    gates this card on the named siblings reaching Done, without a follow-up
    REST PATCH."""
    sibling = await m.create_card("P", "Sibling", confirm_new_project=True)
    card = await m.create_card(
        "P", "Gated", depends_on=[sibling["id"]],
    confirm_new_project=True)
    assert card["depends_on"] == [sibling["id"]]

    # Reload via get_card to make sure the value landed in storage, not just
    # the in-memory response.
    fetched = await m.get_card(card["id"])
    assert fetched["depends_on"] == [sibling["id"]]


@pytest.mark.asyncio
async def test_mcp_create_card_omitted_depends_on_stays_none():
    """Omitting depends_on on create must leave the column None (backwards
    compat — pre-existing MCP callers don't suddenly sprout a list field)."""
    card = await m.create_card("P", "Standalone", confirm_new_project=True)
    assert card["depends_on"] is None


@pytest.mark.asyncio
async def test_mcp_update_card_accepts_depends_on_and_round_trips():
    """update_card(card_id, depends_on=[...]) must write the new list through
    apply_operation("update") → _materialize, the same path the REST PATCH
    endpoint uses. Setting and replacing both work."""
    card = await m.create_card("P", "Gated", confirm_new_project=True)
    assert card["depends_on"] is None

    a = await m.create_card("P", "A", confirm_new_project=True)
    updated = await m.update_card(card["id"], depends_on=[a["id"]])
    assert updated["depends_on"] == [a["id"]]

    b = await m.create_card("P", "B", confirm_new_project=True)
    replaced = await m.update_card(card["id"], depends_on=[a["id"], b["id"]])
    assert replaced["depends_on"] == [a["id"], b["id"]]


@pytest.mark.asyncio
async def test_mcp_update_card_omitted_depends_on_preserves_existing():
    """update_card(card_id, title=...) with no depends_on arg must not clobber
    a previously-set depends_on — same "skip-when-None" semantics as the
    other updatable fields (title/description/metadata)."""
    card = await m.create_card("P", "Gated", confirm_new_project=True)
    a = await m.create_card("P", "A", confirm_new_project=True)
    await m.update_card(card["id"], depends_on=[a["id"]])

    # Title-only update — must not touch depends_on.
    updated = await m.update_card(card["id"], title="Renamed gated card")
    assert updated["depends_on"] == [a["id"]]
    assert updated["title"] == "Renamed gated card"


@pytest.mark.asyncio
async def test_mcp_create_card_depends_on_survives_rematerialize():
    """depends_on must survive the op-log → materialized-row replay that
    rematerialize() performs — otherwise a DB rebuild would silently drop
    the sibling-dep wiring, leaving the dispatcher to skip a card that
    shouldn't be dispatchable yet."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    sibling = await m.create_card("P", "Sibling", confirm_new_project=True)
    gated = await m.create_card(
        "P", "Gated", depends_on=[sibling["id"]],
    confirm_new_project=True)

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()

    fetched = await m.get_card(gated["id"])
    assert fetched["depends_on"] == [sibling["id"]]


# --- set_card_gate: machine-readable business gate (card f8ef71a0…) ----
#
# The gate is independent of `depends_on`: it lets an operator pin a card
# against a *business* trigger (e.g. "activeert pas bij tweede-executor-
# provider-onboarding") that has no kanban-card representation. The
# dispatcher (`dispatch._is_gated`) reads `card.metadata["gated_on"]` on
# every tick and holds the card out of auto-dispatch while it is non-empty.
# See `docs/cockpit/kanban-conventions.md` §4 and
# `tests/test_dispatch_gate.py` for the dispatch-side regressions.


@pytest.mark.asyncio
async def test_set_card_gate_sets_metadata_and_posts_audit_comment():
    """set_card_gate writes ``metadata.gated_on`` verbatim AND posts a
    `**Gate:** set — <trigger>` activity-feed comment so the gate's history
    is visible without inspecting metadata. The comment prefix matches the
    kanban-conventions pattern; ``enrich_done_info`` ignores it (no Done
    marker collision)."""
    created = await m.create_card("P", "Gated spike", "desc", confirm_new_project=True)
    cid = created["id"]

    res = await m.set_card_gate(cid, "second-executor-provider-onboarded")
    assert res["metadata"]["gated_on"] == "second-executor-provider-onboarded"

    # Fetch the card's op-log and confirm the audit comment landed. Use
    # `card_activity` (the same helper used by Kanban UI for the activity
    # feed) rather than reading the response — get_card returns the
    # CardResponse shape with no `activity` field.
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import card_activity
    async with KanbanSessionLocal() as s:
        activity = await card_activity(s, cid)
    gate_comments = [
        op for op in activity
        if op.op_type == "comment"
        and op.payload.get("text", "").startswith("**Gate:**")
    ]
    assert len(gate_comments) == 1
    assert "second-executor-provider-onboarded" in gate_comments[0].payload["text"]


@pytest.mark.asyncio
async def test_set_card_gate_clear_with_none_removes_key():
    """Passing gated_on=None lifts the gate: ``metadata.gated_on`` is removed
    (not set to None — JSON null would be a different sentinel). The card
    becomes dispatchable again on the next tick."""
    created = await m.create_card("P", "Gated", "desc", confirm_new_project=True)
    cid = created["id"]

    await m.set_card_gate(cid, "trigger-x")
    fetched = await m.get_card(cid)
    assert fetched["metadata"]["gated_on"] == "trigger-x"

    res = await m.set_card_gate(cid, None)
    assert "gated_on" not in (res["metadata"] or {}), (
        "gated_on must be deleted, not set to null — _is_gated's "
        "falsy-check would treat both the same way, but storing None "
        "as the literal value would muddle future intent. Use a "
        "missing key to signal 'no gate'."
    )


@pytest.mark.asyncio
async def test_set_card_gate_clear_with_empty_string_treated_as_clear():
    """An empty string normalizes to None (lift the gate). Mirrors
    ``_is_gated``'s fail-open behaviour on empty values — a typo at the
    call site doesn't wedge the card forever."""
    created = await m.create_card("P", "Gated", "desc", confirm_new_project=True)
    cid = created["id"]

    await m.set_card_gate(cid, "trigger-x")
    res = await m.set_card_gate(cid, "")
    assert "gated_on" not in (res["metadata"] or {})


@pytest.mark.asyncio
async def test_set_card_gate_preserves_other_metadata_keys():
    """The gate tool only touches ``metadata.gated_on``; other keys
    (external ids, owner, workflow tags) must survive the round-trip.
    Operators compose gates with other integration metadata; nuking
    them on every gate flip would be a footgun."""
    created = await m.create_card("P", "With metadata", "desc", confirm_new_project=True)
    cid = created["id"]

    # Seed some unrelated metadata via update_card.
    await m.update_card(cid, metadata={"external_ref": "JIRA-123", "owner": "team-x"})
    # Now set a gate — pre-existing keys must survive.
    await m.set_card_gate(cid, "trigger-y")
    fetched = await m.get_card(cid)
    md = fetched["metadata"] or {}
    assert md.get("gated_on") == "trigger-y"
    assert md.get("external_ref") == "JIRA-123"
    assert md.get("owner") == "team-x"

    # Lifting the gate must also leave the unrelated keys alone.
    await m.set_card_gate(cid, None)
    fetched = await m.get_card(cid)
    md = fetched["metadata"] or {}
    assert "gated_on" not in md
    assert md.get("external_ref") == "JIRA-123"
    assert md.get("owner") == "team-x"


@pytest.mark.asyncio
async def test_set_card_gate_returns_not_found_for_missing_card():
    res = await m.set_card_gate("does-not-exist", "trigger-x")
    assert res.get("error") == "not_found"
    assert res.get("card_id") == "does-not-exist"


# --- labels on create_card / update_card ------------------------------------
# Regression: `labels` exists end-to-end — KanbanCard.labels
# (models.py:55), CardCreate/CardUpdate/CardResponse (schemas.py:179,200,119),
# materialized by operations.py:137,201, rendered by CardItem.tsx:234,
# editable by humans in the UI. The MCP update_card / create_card tools,
# however, didn't surface labels, so a dispatched agent had no way to set
# them — the write path existed only via REST PATCH or a human in the UI.
# Surface the field on both tools with replace-semantics, consistent with
# `depends_on` (the dispatcher reads it back via get_card → CardResponse).


@pytest.mark.asyncio
async def test_mcp_create_card_accepts_labels_and_persists_them():
    """A `labels=[...]` passed at create time must round-trip through the
    create op-log and be visible on the resulting card — so a downstream
    agent can rely on labels being there without a follow-up PATCH."""
    card = await m.create_card("P", "Tagged", labels=["urgent", "backend"], confirm_new_project=True)
    assert card["labels"] == ["urgent", "backend"]

    # Reload via get_card to make sure the value landed in storage, not just
    # the in-memory response — mirrors the depends_on regression above.
    fetched = await m.get_card(card["id"])
    assert fetched["labels"] == ["urgent", "backend"]


@pytest.mark.asyncio
async def test_mcp_create_card_omitted_labels_stays_none():
    """Omitting labels on create must leave the column None (backwards
    compat — pre-existing MCP callers don't suddenly sprout a list field)."""
    card = await m.create_card("P", "Standalone", confirm_new_project=True)
    assert card["labels"] is None


@pytest.mark.asyncio
async def test_mcp_update_card_accepts_labels_and_replaces_existing():
    """update_card(card_id, labels=[...]) must write the new list through
    apply_operation("update") → _materialize, the same path the REST PATCH
    endpoint uses. Setting and replacing both work, and the operation is a
    full replace — passing a new list clobbers the previous one (matches
    the explicit "vervang-semantiek" the docstring spells out)."""
    card = await m.create_card("P", "Will be tagged", labels=["alpha"], confirm_new_project=True)
    assert card["labels"] == ["alpha"]

    replaced = await m.update_card(card["id"], labels=["beta", "gamma"])
    assert replaced["labels"] == ["beta", "gamma"]

    # Setting again overwrites — agents must read the docstring to know
    # the operation is replace, not append.
    replaced_again = await m.update_card(card["id"], labels=["delta"])
    assert replaced_again["labels"] == ["delta"]


@pytest.mark.asyncio
async def test_mcp_update_card_labels_with_empty_list_clears_existing():
    """Passing `labels=[]` (an explicit empty list, not None) must clear any
    previously-set labels. This is the standard "clear the labels" path;
    None means "don't touch", empty list means "set to []"."""
    card = await m.create_card("P", "Tagged", labels=["to-clear"], confirm_new_project=True)
    assert card["labels"] == ["to-clear"]

    cleared = await m.update_card(card["id"], labels=[])
    assert cleared["labels"] == []


@pytest.mark.asyncio
async def test_mcp_update_card_omitted_labels_preserves_existing():
    """update_card(card_id, title=...) with no labels arg must not clobber
    a previously-set labels list — same "skip-when-None" semantics as
    title/description/depends_on/metadata. The replace semantics only
    trigger when the caller explicitly passes a value (including [])."""
    card = await m.create_card("P", "Tagged", labels=["keep-me"], confirm_new_project=True)

    # Title-only update — must not touch labels.
    updated = await m.update_card(card["id"], title="Renamed")
    assert updated["labels"] == ["keep-me"]
    assert updated["title"] == "Renamed"


@pytest.mark.asyncio
async def test_mcp_update_card_explicit_none_for_labels_also_preserves_existing():
    """A caller who passes `labels=None` explicitly must hit the same
    "skip" branch — the MCP tool uses `None` as its "field absent" signal,
    so this matches the contract documented in the existing title/
    description paths."""
    card = await m.create_card("P", "Tagged", labels=["keep"], confirm_new_project=True)
    updated = await m.update_card(card["id"], labels=None)
    assert updated["labels"] == ["keep"]


@pytest.mark.asyncio
async def test_mcp_update_card_labels_round_trip_via_card_dict():
    """Labels must round-trip through the JSON-serialised _card_dict payload
    that the MCP server returns — same as the op-log replay path. Verifies
    the schema (CardResponse.labels) and the materializer agree on the
    field name so an agent that reads the response gets back the same list
    it sent in."""
    card = await m.create_card("P", "Round-trip", labels=["x", "y"], confirm_new_project=True)
    fetched = await m.get_card(card["id"])
    # list (not stringified JSON) — CardResponse.labels is typed as `list`.
    assert isinstance(fetched["labels"], list)
    assert fetched["labels"] == ["x", "y"]

    updated = await m.update_card(card["id"], labels=["z"])
    assert isinstance(updated["labels"], list)
    assert updated["labels"] == ["z"]


@pytest.mark.asyncio
async def test_mcp_create_card_labels_survive_rematerialize():
    """Labels must survive the op-log → materialized-row replay that
    rematerialize() performs — same regression class as the depends_on
    test above. Otherwise a DB rebuild would silently drop the labels
    column and any UI / routing logic that reads them would break."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    card = await m.create_card("P", "Tagged", labels=["survives"], confirm_new_project=True)

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()

    fetched = await m.get_card(card["id"])
    assert fetched["labels"] == ["survives"]


@pytest.mark.asyncio
async def test_mcp_update_card_labels_survive_rematerialize():
    """Same regression for the update path: labels set via update_card
    must persist through rematerialize()."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    card = await m.create_card("P", "Will be tagged", confirm_new_project=True)
    await m.update_card(card["id"], labels=["alpha", "beta"])

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()

    fetched = await m.get_card(card["id"])
    assert fetched["labels"] == ["alpha", "beta"]


# --- unknown project_key validation (kanban card 91c85199) ------------------
#
# A mistyped or guessed `project` used to fail silently: list_cards returned
# an empty list indistinguishable from "this project's Backlog is really
# empty" (the incident: a dedup pass almost mass-duplicated 36 cards after
# reading a typo'd project key as an empty board), and create_card would
# quietly create an orphaned card in a bucket auto-dispatch never sees.
# Both tools now validate `project` against known keys (existing cards or
# columns) and refuse an unknown one — create_card offers an explicit
# `confirm_new_project=True` opt-in for a project's genuine first card.

@pytest.mark.asyncio
async def test_list_cards_unknown_project_key_returns_error_not_empty_list():
    result = await m.list_cards("git:github.com/typo-org/claude-cockpit")
    assert result["error"] == "unknown_project_key"
    assert result["project"] == "git:github.com/typo-org/claude-cockpit"


@pytest.mark.asyncio
async def test_list_cards_known_project_key_is_unaffected():
    cid = (await m.create_card("KNOWN-PROJ", "t", "",
                                confirm_new_project=True))["id"]
    listed = await m.list_cards("KNOWN-PROJ")
    assert any(c["id"] == cid for c in listed)


@pytest.mark.asyncio
async def test_create_card_unknown_project_key_is_refused_without_confirm():
    result = await m.create_card("git:github.com/typo-org/claude-cockpit",
                                  "Should not be created", "")
    assert result["error"] == "unknown_project_key"
    listed = await m.list_cards("git:github.com/typo-org/claude-cockpit")
    assert listed["error"] == "unknown_project_key", (
        "the refused create_card must not have silently created the "
        "project's first card either"
    )


@pytest.mark.asyncio
async def test_create_card_new_project_allowed_with_explicit_confirm():
    card = await m.create_card("BRAND-NEW-PROJ", "First card", "",
                                confirm_new_project=True)
    assert "error" not in card
    listed = await m.list_cards("BRAND-NEW-PROJ")
    assert any(c["id"] == card["id"] for c in listed)


@pytest.mark.asyncio
async def test_create_card_second_card_for_known_project_needs_no_confirm():
    """Once a project has ≥1 card, subsequent create_card calls for the same
    key don't need confirm_new_project — only the very first card does."""
    first = await m.create_card("ALREADY-KNOWN", "First", "",
                                 confirm_new_project=True)
    second = await m.create_card("ALREADY-KNOWN", "Second", "")
    assert "error" not in second
    assert second["id"] != first["id"]


# --- scheduled_at: kanban card `c7367319b9d245bdbd4cdc2ddc93e134` ---------
#
# The MCP `create_card` wrapper used to silently drop `scheduled_at`, so a
# cadence-chain successor created via MCP landed immediately dispatchable
# instead of sleeping until its intended time. The REST POST already accepts
# the field; the MCP surface now mirrors it. The test below pins both halves
# of the acceptance criterion: (a) the field round-trips, and (b) an
# unparseable value is rejected with a clear error rather than silently
# mis-routed.

@pytest.mark.asyncio
async def test_create_card_with_scheduled_at_round_trips_and_is_not_due():
    """An MCP `create_card(scheduled_at=...)` stores the value on the card
    AND keeps it out of the auto-dispatch pool (dep_resolver.is_due must
    report False for a not-yet-reached future time).

    Both halves matter independently:
      - Without round-trip, the field is a silent no-op (the original bug).
      - Without the dispatch hold, the field lands but the next 10s tick
        claims it and re-runs the sweep — the recurring-loop half of the
        incident in kanban card `c7367319b9d245bdbd4cdc2ddc93e134`.
    """
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dep_resolver import is_due as dep_is_due

    # Far-future timestamp: far enough to dodge the 2-second resume-race
    # guard that `set_resume` may stamp on freshly-created cards, and far
    # enough to never trip the "now" half of is_due in a slow CI runner.
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    card = await m.create_card(
        "SCHED", "Cadence successor", "",
        work_type="analysis",
        scheduled_at=future,
        confirm_new_project=True,
    )
    assert "error" not in card, card
    assert card["scheduled_at"] == future, (
        f"MCP create_card dropped scheduled_at — got {card.get('scheduled_at')!r}"
    )

    # Read back through the ORM and confirm the dispatch hold takes effect.
    async with KanbanSessionLocal() as s:
        from app.kanban.service import get_card as service_get_card
        row = await service_get_card(s, card["id"])
        assert row.scheduled_at == future
        assert dep_is_due(row) is False, (
            "card with future scheduled_at must NOT be dispatchable yet "
            "(dep_resolver.is_due returned True)"
        )


@pytest.mark.asyncio
async def test_create_card_with_unparseable_scheduled_at_is_rejected():
    """A non-ISO-8601 `scheduled_at` is refused with a clear error, not
    silently coerced to None / stored verbatim / left to dep_resolver's
    fail-open (which would make the cadence-successor bug bite again).

    Acceptance criterion 2 of kanban card `c7367319b9d245bdbd4cdc2ddc93e134`:
    "Een ongeldige/niet-parseerbare waarde wordt geweigerd met een duidelijke
    fout in plaats van stil genegeerd." The MCP layer must surface this so
    the operator notices; dep_resolver.is_due's fail-open stays in place for
    legacy rows, but new writes cannot introduce fresh garbage."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import get_card as service_get_card

    result = await m.create_card(
        "SCHED-BAD", "Bad schedule", "",
        scheduled_at="not-a-date",
        confirm_new_project=True,
    )
    assert result.get("error") == "invalid_scheduled_at", (
        f"MCP create_card accepted a non-ISO-8601 scheduled_at: {result!r}"
    )
    msg = result.get("message", "")
    assert "scheduled_at" in msg, (
        f"error message must name the offending field; got: {msg!r}"
    )

    # And nothing was created on the refused call — the failed validation
    # must not have leaked a half-built card into the Backlog.
    async with KanbanSessionLocal() as s:
        leaked = await service_get_card(s, result.get("card_id", ""))
        assert leaked is None, (
            "rejected create_card must not persist a card "
            f"(found row id={result.get('card_id')!r})"
        )


@pytest.mark.asyncio
async def test_create_card_without_scheduled_at_remains_due():
    """The omitted case stays untouched — no scheduled_at → dep_resolver
    returns True, so a plain `create_card` keeps its existing dispatch
    behaviour. Guards against an over-broad validator breaking the default
    path."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dep_resolver import is_due as dep_is_due

    card = await m.create_card(
        "NO-SCHED", "Plain card", "",
        work_type="analysis",
        confirm_new_project=True,
    )
    assert "error" not in card
    assert card.get("scheduled_at") is None

    async with KanbanSessionLocal() as s:
        from app.kanban.service import get_card as service_get_card
        row = await service_get_card(s, card["id"])
        assert dep_is_due(row) is True
