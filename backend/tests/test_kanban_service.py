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
