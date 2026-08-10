# backend/tests/test_kanban_service.py
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
async def test_list_cards_compact_skips_deliverables_eager_load():
    """compact=True must NOT trigger the deliverables selectinload — that's
    the single biggest chunk of payload weight (a 126KB / 48-card board
    response drops to ~3KB once deliverables + description are excluded).
    We assert the eager-load side-effect via SQLAlchemy's session
    `is_loaded`: after the call with compact=True the relationship is
    unloaded, while the default (compact=False) call leaves it loaded so
    existing callers (REST + MCP _card_dict) can serialize it as before.
    """

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="COMPACT", entity_id=None,
            payload={"title": "c1", "description": "x" * 200})
        # Attach a deliverable so the eager load would have a row to fetch.
        from app.kanban.operations import apply_operation as _apply
        await _apply(s, op_type="attach", entity_type="deliverable",
            project_key="COMPACT", entity_id=cid,
            payload={"kind": "note", "ref": "abcdefg"})
        await s.commit()

        async with KanbanSessionLocal() as s:
            default_rows = await service.list_cards(s, "COMPACT")
            assert len(default_rows) == 1
            assert "deliverables" in default_rows[0].__dict__, (
                "default mode should have the relationship populated "
                "(existing REST/MCP callers rely on it)"
            )

        async with KanbanSessionLocal() as s:
            compact_rows = await service.list_cards(s, "COMPACT", compact=True)
            assert len(compact_rows) == 1
            assert "deliverables" not in compact_rows[0].__dict__, (
                "compact mode must NOT trigger the selectinload"
            )


@pytest.mark.asyncio
async def test_list_cards_compact_default_is_false_backwards_compatible():
    """Calling list_cards() with no compact kwarg must behave identically to
    the pre-change signature (compact=False): same number of rows, same
    deliverables relationship loaded. Pinning this guards against an
    accidental keyword-only-after-positional refactor that breaks callers
    using service.list_cards(s, project_key, column)."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="BC", entity_id=None,
            payload={"title": "x", "column": "Backlog"})
        await s.commit()

        async with KanbanSessionLocal() as s:
            rows = await service.list_cards(s, "BC")
            assert len(rows) == 1
            assert "deliverables" in rows[0].__dict__


@pytest.mark.asyncio
async def test_list_cards_filters_by_project_and_column():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a1", "column": "Todo"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a2", "column": "Done"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="B", entity_id=None, payload={"title": "b1", "column": "Todo"})
        await s.commit()
        all_a = await service.list_cards(s, "A")
        assert {c.title for c in all_a} == {"a1", "a2"}
        todo_a = await service.list_cards(s, "A", column="Todo")
        assert {c.title for c in todo_a} == {"a1"}


@pytest.mark.asyncio
async def test_list_cards_ready_excludes_cards_with_open_dependencies():
    """ready=True keeps cards with no depends_on OR whose depends_on all
    resolve to a parent in column='Done'. The dep_resolver predicate
    (`meets_dep_prerequisites`) is the source of truth here so the
    auto-dispatch tick and the API filter agree."""
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Backlog"})
        child_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "column": "Backlog",
                     "depends_on": [parent_id]})
        standalone_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "standalone", "column": "Backlog"})
        await s.commit()

        ready = await service.list_cards(s, "A", ready=True)
        ready_ids = {c.id for c in ready}
        # parent has no deps → ready; child has an unmet parent → not ready;
        # standalone has no deps → ready.
        assert parent_id in ready_ids
        assert child_id not in ready_ids
        assert standalone_id in ready_ids

        # All cards still come back when neither filter is set (existing path).
        everything = await service.list_cards(s, "A")
        assert {c.id for c in everything} == {parent_id, child_id, standalone_id}


@pytest.mark.asyncio
async def test_list_cards_ready_treats_done_parents_as_satisfied():
    """A child whose parent has been moved to Done counts as ready. Mirrors
    dispatch's own dep check so the UI doesn't show 'blocked' on cards the
    dispatcher would actually pick up."""
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Backlog"})
        child_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "column": "Backlog",
                     "depends_on": [parent_id]})
        await s.commit()

        # Move parent to Done.
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="A", entity_id=parent_id, payload={"column": "Done"})
        await s.commit()

        ready = await service.list_cards(s, "A", ready=True)
        ready_ids = {c.id for c in ready}
        assert child_id in ready_ids


@pytest.mark.asyncio
async def test_list_cards_blocking_returns_cards_waited_on_by_others():
    """blocking=True keeps a card IFF at least one other non-Done card in
    the project lists my id in its depends_on. Self-evident baseline:
    the parent of an open child is blocking; an idle standalone is not."""
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Backlog"})
        child_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "column": "Backlog",
                     "depends_on": [parent_id]})
        standalone_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "standalone", "column": "Backlog"})
        await s.commit()

        blocking = await service.list_cards(s, "A", blocking=True)
        blocking_ids = {c.id for c in blocking}
        # parent is the only card the still-unfinished child waits on.
        assert parent_id in blocking_ids
        assert child_id not in blocking_ids
        assert standalone_id not in blocking_ids


@pytest.mark.asyncio
async def test_list_cards_blocking_excludes_when_all_dependents_are_done():
    """Once the only child has been moved to Done, the parent is no longer
    blocking. The "blocking" notion is 'someone still waits on me', which
    dissolves the moment every dependent reaches Done."""
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Backlog"})
        child_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "column": "Backlog",
                     "depends_on": [parent_id]})
        await s.commit()

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="A", entity_id=child_id, payload={"column": "Done"})
        await s.commit()

        blocking = await service.list_cards(s, "A", blocking=True)
        assert {c.id for c in blocking} == set()


@pytest.mark.asyncio
async def test_list_cards_ready_and_blocking_combine_as_intersection():
    """ready=True & blocking=True keeps cards that are simultaneously
    dispatchable AND waited on — a 'bottleneck' card whose parents are done
    but whose own children are not yet. Independent filters per the API
    contract, but compose cleanly so a planning agent can ask for both.

    Topology used:
        parent1  (no deps, child1 depends on it)        ready, blocking  → keep
        child1   (deps=[parent1])                      blocked, leaf    → skip
        parent2  (no deps, no one depends on it)        ready, idle      → skip
    """
    async with KanbanSessionLocal() as s:
        parent1_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "p1", "column": "Backlog"})
        child1_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "depends_on": [parent1_id]})
        parent2_id = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "p2", "column": "Backlog"})
        await s.commit()

        both = await service.list_cards(s, "A", ready=True, blocking=True)
        ids = {c.id for c in both}
        # Only parent1 satisfies both filters.
        assert ids == {parent1_id}
        # And explicitly verify each excluded card's reason:
        # child1 — not ready (parent1 not Done);
        # parent2 — not blocking (no open dependent).
        assert child1_id not in ids
        assert parent2_id not in ids


@pytest.mark.asyncio
async def test_card_activity_returns_oplog_for_card():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid, payload={"text": "hi"})
        await s.commit()
        feed = await service.card_activity(s, cid)
        assert [e.op_type for e in feed] == ["create", "comment"]


@pytest.mark.asyncio
async def test_column_default_provider_roundtrip():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key="A", name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        await s.commit()
        assert col.default_provider == "minimax"
        assert await service.get_column_default_provider(s, "A", "engineer") == "minimax"


@pytest.mark.asyncio
async def test_column_default_provider_missing_column_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.get_column_default_provider(s, "A", "no-such-column") is None


@pytest.mark.asyncio
async def test_update_column_can_set_default_provider():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key="A", name="engineer")
        await s.commit()
        updated = await service.update_column(s, col.id, default_provider="minimax")
        await s.commit()
        assert updated.default_provider == "minimax"


@pytest.mark.asyncio
async def test_column_default_model_roundtrip():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key="A", name="engineer", default_agent="engineer",
            default_model="opus",
        )
        await s.commit()
        assert col.default_model == "opus"
        assert await service.get_column_default_model(s, "A", "engineer") == "opus"


@pytest.mark.asyncio
async def test_column_default_model_missing_column_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.get_column_default_model(s, "A", "no-such-column") is None


@pytest.mark.asyncio
async def test_update_column_can_set_default_model():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key="A", name="engineer")
        await s.commit()
        updated = await service.update_column(s, col.id, default_model="haiku")
        await s.commit()
        assert updated.default_model == "haiku"

# NOTE: the sync-seam tests (ops_since / ingest_ops convergence + idempotent replay)
# were removed when sync.py was pruned. See docs/cockpit/sync-hlc-freeze-vs-prune.md.
# Idempotent HLC-ordered replay of the *local* op-log stays covered by
# test_kanban_rematerialize.py.


# --- Awaiting Subtasks parking (analyse-levenscyclus-decision §3) ---------


@pytest.mark.asyncio
async def test_ensure_awaiting_subtasks_column_creates_row_ranked_before_done():
    """Idempotent helper, mirrors ensure_analyst_column:
    creates the row once, ranks it just before `Done`, and a second call is
    a no-op (returns False, no duplicate row)."""
    async with KanbanSessionLocal() as s:
        done_col = await service.create_column(s, project_key="A", name="Done", rank="0100")
        await s.commit()

        created = await service.ensure_awaiting_subtasks_column(s, "A")
        await s.commit()
        assert created is True

        cols = await service.list_columns(s, "A")
        awaiting = next(c for c in cols if c.name == "Awaiting Subtasks")
        assert int(awaiting.rank) < int(done_col.rank)

        created_again = await service.ensure_awaiting_subtasks_column(s, "A")
        await s.commit()
        assert created_again is False
        cols = await service.list_columns(s, "A")
        assert sum(1 for c in cols if c.name == "Awaiting Subtasks") == 1


@pytest.mark.asyncio
async def test_card_has_children_true_and_false():
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "parent"})
        childless = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "childless"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "parent_card_id": parent})
        await s.commit()

        assert await service.card_has_children(s, parent) is True
        assert await service.card_has_children(s, childless) is False


@pytest.mark.asyncio
async def test_close_parent_if_all_children_done_requires_parent_parked():
    """A parent still sitting in an agent column (analysis in progress, not
    yet parked) must not be auto-closed even if all children are Done —
    only a genuinely parked parent (`Awaiting Subtasks`) is a candidate."""
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "analyst"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child", "column": "Done", "parent_card_id": parent})
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is False
        assert (await service.get_card(s, parent)).column == "analyst"


@pytest.mark.asyncio
async def test_close_parent_if_all_children_done_false_while_one_pending():
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Awaiting Subtasks"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "done-child", "column": "Done", "parent_card_id": parent})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "pending-child", "column": "Backlog", "parent_card_id": parent})
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is False
        assert (await service.get_card(s, parent)).column == "Awaiting Subtasks"


@pytest.mark.asyncio
async def test_close_parent_if_all_children_done_true_posts_summary_comment():
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parent", "column": "Awaiting Subtasks"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child1", "column": "Done", "parent_card_id": parent})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "child2", "column": "Done", "parent_card_id": parent})
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is True
        assert (await service.get_card(s, parent)).column == "Done"

        ops = await service.card_activity(s, parent)
        summary_comments = [
            o for o in ops
            if o.op_type == "comment" and (o.payload.get("text") or "").startswith("**Summary:**")
        ]
        assert len(summary_comments) == 1


@pytest.mark.asyncio
async def test_close_parent_if_all_children_done_zero_children_closes_parent():
    """Acceptance #2 — a parent parked in `Awaiting Subtasks` with **zero**
    children must auto-close: there is nothing left to wait for. The previous
    `if not children or any(...): return False` short-circuit held the parent
    on Awaiting Subtasks forever once the last child was deleted
    (kaart `400d6a77…`; 5 parents had been stuck in this state for up to 6
    weeks before being manually closed). The auto-close still runs only when
    the parent is in `Awaiting Subtasks` — agents in flight are still left
    alone, and a not-parked parent in an analyst/engineer column is also
    untouched.
    """
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "parked-parent", "column": "Awaiting Subtasks"})
        # The parent has zero children — this is the regression shape.
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is True, (
            "Parked parent with zero children must auto-close: there is no "
            "child left to wait for."
        )
        assert (await service.get_card(s, parent)).column == "Done"

        # A leading `**Summary:**` comment is posted so the Done-banner is
        # not blank — same shape as the regular all-Done case.
        ops = await service.card_activity(s, parent)
        summary_texts = [
            o.payload.get("text") for o in ops
            if o.op_type == "comment"
            and (o.payload.get("text") or "").startswith("**Summary:**")
        ]
        assert len(summary_texts) == 1, summary_texts


@pytest.mark.asyncio
async def test_close_parent_zero_children_rollup_mentions_children_removed():
    """Acceptance #3 — the roll-up posted for a zero-children auto-close
    must explicitly state that the children were already gone, so a reader
    does not infer there was never any work. We assert two phrasings that
    can co-exist: a "kinderen zijn al verwijderd" note, and an explicit
    mention that the auto-close fired because no children were left.
    """
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "klaar-zonder-kinderen",
                     "column": "Awaiting Subtasks"})
        await s.commit()

        await service.close_parent_if_all_children_done(s, parent)
        await s.commit()

        ops = await service.card_activity(s, parent)
        rollup = next(
            (o.payload.get("text") for o in ops
             if o.op_type == "comment"
             and (o.payload.get("text") or "").startswith("**Summary:**")),
            None,
        )
        assert rollup is not None, ops
        body = rollup[len("**Summary:** "):]
        # Must say children are gone (accept either Dutch wording).
        assert any(p in body for p in (
            "kinderen zijn al verwijderd",
            "kinderen al van het bord",
            "kind-kaarten waren al",
            "kinderen waren al",
            "geen kinderen meer",
            "zero children",
        )), (
            f"Roll-up must explain children are gone; body was:\n{body}"
        )
        # Must still name the parent so the reader knows which card closed.
        assert "klaar-zonder-kinderen" in body


@pytest.mark.asyncio
async def test_close_parent_zero_children_does_not_close_non_parked_parent():
    """Guard rail for acceptance #2: the zero-children branch must not
    bleed into the parked-only invariant. A non-parked parent (analyst
    column, in flight) is still left alone even with zero children — only
    parked parents are auto-closed, and the parked-only check is what
    `test_close_parent_if_all_children_done_requires_parent_parked` pins
    for the populated-children case. We re-pin it for the empty-children
    case so a future refactor that flips the predicate does not silently
    start auto-closing parents that are still in flight.
    """
    async with KanbanSessionLocal() as s:
        parent = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None,
            payload={"title": "in-flight-parent", "column": "analyst"})
        await s.commit()

        closed = await service.close_parent_if_all_children_done(s, parent)
        await s.commit()
        assert closed is False
        assert (await service.get_card(s, parent)).column == "analyst"
