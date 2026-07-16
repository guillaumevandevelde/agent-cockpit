# backend/tests/test_kanban_operations.py
import gc

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban import models
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_can_persist_an_op_row():
    async with KanbanSessionLocal() as s:
        s.add(models.KanbanOp(
            op_id="dev-a:1", device_id="dev-a", seq=1, hlc="1:0:dev-a",
            project_key="git:example", entity_type="card", entity_id="c1",
            op_type="create", payload={"title": "x", "column": "Backlog"},
        ))
        await s.commit()
        rows = (await s.execute(select(models.KanbanOp))).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload["title"] == "x"


from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation, get_device_id, release_card_claim


@pytest.mark.asyncio
async def test_create_card_materializes_a_card_row():
    async with KanbanSessionLocal() as s:
        card_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "First", "description": "d", "column": "Backlog"},
        )
        await s.commit()
        card = await s.get(KanbanCard, card_id)
        assert card is not None
        assert card.title == "First"
        assert card.column == "Backlog"
        assert card.title_hlc is not None


@pytest.mark.asyncio
async def test_device_id_is_stable():
    async with KanbanSessionLocal() as s:
        a = await get_device_id(s)
        b = await get_device_id(s)
        await s.commit()
        assert a == b and len(a) > 0


@pytest.mark.asyncio
async def test_move_updates_column_with_lww():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None,
            payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Doing"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "Doing"


@pytest.mark.asyncio
async def test_stale_move_is_ignored_by_lww():
    # An op with an older HLC than the field's current HLC must not win.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        card = await s.get(KanbanCard, cid)
        card.column = "Review"
        card.column_hlc = "9999999999999:00000:dev-z"  # far-future HLC
        await s.flush()
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Done"})
        await s.commit()
        refreshed = await s.get(KanbanCard, cid)
        assert refreshed.column == "Review"  # stale move rejected


@pytest.mark.asyncio
async def test_update_title_and_description():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "old"})
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="p", entity_id=cid,
            payload={"title": "new", "description": "desc"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.title == "new"
        assert card.description == "desc"


@pytest.mark.asyncio
async def test_create_card_with_scheduled_at():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None,
            payload={"title": "t", "scheduled_at": "2099-01-01T00:00:00+00:00"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.scheduled_at == "2099-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_update_scheduled_at():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="p", entity_id=cid,
            payload={"scheduled_at": "2099-06-01T12:00:00+00:00"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.scheduled_at == "2099-06-01T12:00:00+00:00"

        # Clearing the schedule (explicit None) removes it.
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="p", entity_id=cid, payload={"scheduled_at": None})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.scheduled_at is None


from app.kanban.operations import ClaimRejected


@pytest.mark.asyncio
async def test_claim_sets_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "sess1@devA"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "sess1@devA"


@pytest.mark.asyncio
async def test_second_claim_is_rejected():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "first@devA"})
        with pytest.raises(ClaimRejected):
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": "second@devB"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "first@devA"


@pytest.mark.asyncio
async def test_concurrent_claim_second_loser_is_rejected_after_first_commits():
    # TOCTOU race: two independent sessions both load the card BEFORE either
    # writes. The first commits a claim; the second still holds the unclaimed
    # snapshot in its identity map. Without an atomic guard, the second
    # session's `session.get(...)` returns the stale unclaimed object, the
    # in-Python check passes, and the second commit silently overwrites the
    # first claim. The contract is that ClaimRejected must surface here so
    # neither caller is misled into thinking they hold the card.
    async with KanbanSessionLocal() as setup:
        cid = await apply_operation(
            setup, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "race-target"},
        )
        await setup.commit()

    session_a = KanbanSessionLocal()
    session_b = KanbanSessionLocal()
    try:
        # Force the TOCTOU window: both sessions load the card BEFORE either
        # writes, so the second session's `_materialize` reads from a stale
        # identity-map snapshot. (NB: re-calling session.get() between the
        # commits would itself refresh the snapshot and mask the bug; we
        # explicitly avoid that here. Strong refs + gc.collect() prevent the
        # SQLAlchemy identity map's WeakValueDictionary from silently GC'ing
        # the cached object between the two commits.)
        loaded_b = await session_b.get(KanbanCard, cid)
        await session_a.get(KanbanCard, cid)
        gc.collect()
        assert loaded_b.claimed_by is None  # unclaimed snapshot pinned

        await apply_operation(
            session_a, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "agent:a"},
        )
        await session_a.commit()
        # Sanity: the second session still holds the stale unclaimed snapshot.
        assert loaded_b.claimed_by is None

        # The second session still has the unclaimed snapshot in its identity
        # map. The contract says: this must raise, not silently overwrite.
        with pytest.raises(ClaimRejected) as excinfo:
            await apply_operation(
                session_b, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": "agent:b"},
            )
        # The error should name the *actual* current owner, not the loser's id.
        assert excinfo.value.current_owner == "agent:a"
        await session_b.rollback()
    finally:
        await session_a.close()
        await session_b.close()

    # Final state: the first claim is the one that survived.
    async with KanbanSessionLocal() as verify:
        card = await verify.get(KanbanCard, cid)
        assert card.claimed_by == "agent:a"


@pytest.mark.asyncio
async def test_release_clears_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "a@d"})
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="p", entity_id=cid, payload={})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by is None


@pytest.mark.asyncio
async def test_release_without_terminal_move_increments_counter():
    # Reproduces kanban card a70a9272's churn pattern: a claim gets released
    # (via the bare release_card entry point) while the card is still sitting
    # in an agent column, never having reached Done/Impediment.
    # dispatch_failures stays 0 for this (the session didn't crash) — this
    # counter is the one that must see it.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "engineer"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "agent:k-test-1"})
        await release_card_claim(s, card_id=cid, project_key="p")
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.release_without_terminal_move == 1
        assert card.column == "engineer"  # below threshold: not auto-flagged yet


@pytest.mark.asyncio
async def test_release_without_terminal_move_resets_on_terminal_move():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "engineer"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "agent:k-test-1"})
        await release_card_claim(s, card_id=cid, project_key="p")
        # Second claim on the same card actually finishes it this time.
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "agent:k-test-2"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Done"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.release_without_terminal_move == 0
        assert card.column == "Done"


@pytest.mark.asyncio
async def test_second_release_without_terminal_move_auto_flags_to_impediment():
    # 2 claim->release cycles with no terminal move must trip the circuit
    # breaker: the card is auto-moved to Impediment (out of _DISPATCH_COLUMNS)
    # so a third dispatch cannot claim it again, and a visible comment
    # explains why. The counter itself resets as part of that terminal move.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "engineer"})
        for i in range(2):
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": f"agent:k-test-{i}"})
            await release_card_claim(s, card_id=cid, project_key="p")
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "Impediment"
        assert card.claimed_by is None
        assert card.release_without_terminal_move == 0  # reset by the auto-move
        comments = (await s.execute(
            _select(KanbanOp).where(
                KanbanOp.entity_id == cid, KanbanOp.op_type == "comment",
            ))).scalars().all()
        assert any("Auto-flagged" in c.payload.get("text", "") for c in comments)


@pytest.mark.asyncio
async def test_dead_claim_reap_style_release_does_not_count_as_churn():
    # dispatch.py's reap/pause release call sites (dead-claim reaper,
    # stuck-session reaper, resume-later) go through plain apply_operation,
    # NOT release_card_claim — they already have their own circuit breaker
    # (dispatch_failures) or represent a legitimate continuation. Repeating
    # that pattern must not trip *this* breaker too.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "engineer"})
        for i in range(5):
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": f"agent:k-test-{i}"})
            await apply_operation(s, op_type="release", entity_type="card",
                project_key="p", entity_id=cid, payload={})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "engineer"
        assert card.release_without_terminal_move == 0


from sqlalchemy import select as _select

from app.kanban.models import KanbanDeliverable, KanbanOp


@pytest.mark.asyncio
async def test_attach_deliverable_creates_row():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="p", entity_id=cid,
            payload={"kind": "pr", "ref": "https://github.com/u/r/pull/7"})
        await s.commit()
        rows = (await s.execute(_select(KanbanDeliverable))).scalars().all()
        assert len(rows) == 1
        assert rows[0].kind == "pr"
        assert rows[0].card_id == cid


@pytest.mark.asyncio
async def test_comment_op_is_recorded_in_log():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="p", entity_id=cid, payload={"text": "looks good"})
        await s.commit()
        ops = (await s.execute(
            _select(KanbanOp).where(KanbanOp.op_type == "comment"))).scalars().all()
        assert ops[0].payload["text"] == "looks good"


@pytest.mark.asyncio
async def test_mutation_ops_inherit_card_project_key():
    # Callers pass project_key="" on move/claim/etc; the op-log must still
    # record the owning card's key (needed for per-project sync/filtering).
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:proj-a", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Doing"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload={"kind": "note", "ref": "x"})
        await s.commit()
        ops = (await s.execute(_select(KanbanOp))).scalars().all()
        assert {o.project_key for o in ops} == {"git:proj-a"}


@pytest.mark.asyncio
async def test_move_to_fixed_column_releases_dangling_agent_claim():
    # Reproduces a real stuck-Backlog card: dispatcher claims + moves a card
    # into an agent column, then it gets moved back to Backlog (e.g. a UI
    # drag-drop) without an explicit release. Without clearing the claim here,
    # the card is invisible to _next_card (requires unclaimed) *and* to the
    # stale-claim reaper (which skips fixed columns) — permanently blocking
    # auto-dispatch for that card.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "engineer"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "agent:k-test-1234"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Backlog"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "Backlog"
        assert card.claimed_by is None


@pytest.mark.asyncio
async def test_move_to_fixed_column_keeps_human_claim():
    # A human claiming a Backlog card to reserve it (never left a fixed
    # column, no "agent:" prefix) must not be auto-released.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "me@ui"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Backlog"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "me@ui"


@pytest.mark.asyncio
async def test_op_ids_are_unique_across_many_ops():
    async with KanbanSessionLocal() as s:
        for i in range(25):
            await apply_operation(s, op_type="create", entity_type="card",
                project_key="p", entity_id=None, payload={"title": f"c{i}"})
        await s.commit()
        op_ids = [o.op_id for o in (await s.execute(_select(KanbanOp))).scalars().all()]
        assert len(op_ids) == 25
        assert len(set(op_ids)) == 25  # no collisions


@pytest.mark.asyncio
async def test_create_card_persists_multi_agent_fields():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "split-task", "column": "Backlog",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code",
                     "depends_on": ["c1"]},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.analyst_agent_id == "claude-code"
        assert card.executor_agent_id == "mimo-code"
        assert card.parent_card_id is None
        assert card.analyst_run_id is None
        assert card.depends_on == ["c1"]


@pytest.mark.asyncio
async def test_update_card_persists_multi_agent_fields():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card",
            project_key="git:example", entity_id=cid,
            payload={"analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code",
                     "parent_card_id": "parent-1",
                     "depends_on": ["c1", "c2"]},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.analyst_agent_id == "claude-code"
        assert card.executor_agent_id == "mimo-code"
        assert card.parent_card_id == "parent-1"
        assert card.depends_on == ["c1", "c2"]


@pytest.mark.asyncio
async def test_create_card_persists_model():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog", "model": "opus"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.model == "opus"


@pytest.mark.asyncio
async def test_update_card_persists_model():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card",
            project_key="git:example", entity_id=cid,
            payload={"model": "sonnet"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.model == "sonnet"


@pytest.mark.asyncio
async def test_delete_card_with_gate_succeeds():
    # Regression: a card that went through open_gate (even answered/closed)
    # left a KanbanGate row FK-referencing it. clear-column deletes Done cards
    # one by one in a single transaction; with foreign_keys=ON, hitting such a
    # card raised IntegrityError and aborted the whole "Clear Done" operation.
    from app.kanban import service

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Done"},
        )
        await service.create_gate(
            s, card_id=cid, project_key="git:example",
            question="Ship now?", options=["yes", "no"],
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="delete", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()
        assert await s.get(KanbanCard, cid) is None
        gates = await service.list_gates(s, cid)
        assert gates == []
