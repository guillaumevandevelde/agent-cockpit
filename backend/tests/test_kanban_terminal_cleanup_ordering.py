# backend/tests/test_kanban_terminal_cleanup_ordering.py
"""Terminal-column session cleanup must fire *after* the move commits.

The cleanup kills the tmux session hosting the MCP client that issued the
move. When it fired from inside `_materialize` (pre-commit), the kill raced
the caller's own `await commit()`: the client died, its request task was
cancelled, and the whole transaction rolled back — leaving the tmux session
gone but the card still sitting in its agent column, still claimed. The
dispatcher then re-claimed and respawned it on every tick. Card a70a9272
burned 26 spawn cycles this way; its move ops appear in the backend log
(apply_operation logs before commit) but never in the DB.

These tests pin the ordering contract: no cleanup on an uncommitted move,
exactly one cleanup once it lands.
"""
import asyncio

import pytest
import pytest_asyncio

from app.kanban import operations
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.fixture
def fired(monkeypatch):
    """Record cleanup invocations.

    Patched on `app.kanban.operations` — the consumer — because
    `_cleanup_after_commit` does a function-local `from ... import
    on_card_moved_to_done`, so the name is resolved out of the source module
    at call time. Patching the source module is what actually reaches it here;
    every test below asserts the double fired (or deliberately did not), so a
    no-op patch cannot pass silently.
    """
    calls: list[tuple[str, str]] = []
    import app.kanban.session_cleanup as sc

    monkeypatch.setattr(sc, "on_card_moved_to_done",
                        lambda card_id, project_key: calls.append((card_id, project_key)))
    return calls


async def _make_card(s) -> str:
    return await apply_operation(s, op_type="create", entity_type="card",
        project_key="P", entity_id=None,
        payload={"title": "c", "column": "engineer"})


@pytest.mark.asyncio
async def test_no_cleanup_while_move_is_uncommitted(fired):
    """The window that caused the bug: move applied, commit not reached."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Done"})
        # Mid-transaction — exactly where the killed client used to lose the
        # move. Nothing may have killed the session yet.
        await asyncio.sleep(0)
        assert fired == []


@pytest.mark.asyncio
async def test_rolled_back_move_never_cleans_up(fired):
    """A move that never lands must not kill the session it belongs to."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Done"})
        await s.rollback()

    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["Done", "Impediment"])
async def test_committed_move_fires_cleanup_once(fired, column):
    """Both terminal columns still trigger cleanup — once — after commit."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": column})
        await s.commit()

    # `after_commit` hands off via loop.call_soon; let that callback run.
    await asyncio.sleep(0)
    assert fired == [(cid, "P")]


@pytest.mark.asyncio
async def test_committed_move_is_durable(fired):
    """The regression itself: after commit the card is really in Done."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Done"})
        await s.commit()

    await asyncio.sleep(0)
    async with KanbanSessionLocal() as s:
        from app.kanban.models import KanbanCard
        assert (await s.get(KanbanCard, cid)).column == "Done"
    assert fired == [(cid, "P")]


@pytest.mark.asyncio
async def test_non_terminal_move_does_not_clean_up(fired):
    """Only terminal columns end the session — Backlog must not."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Backlog"})
        await s.commit()

    await asyncio.sleep(0)
    assert fired == []
    assert operations._TERMINAL_CLEANUP_COLUMNS == {"Done", "Impediment"}
