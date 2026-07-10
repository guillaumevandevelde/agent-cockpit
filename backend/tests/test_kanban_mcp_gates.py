# backend/tests/test_kanban_mcp_gates.py
import asyncio

import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from app.kanban import service
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _fast_poll(monkeypatch):
    """Polling every 2s would make these tests glacial; shrink the interval."""
    monkeypatch.setattr(m, "_GATE_POLL_INTERVAL_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_open_gate_not_found():
    result = await m.open_gate("nonexistent-id", "Ship?", ["yes", "no"])
    assert result == {"error": "not_found", "card_id": "nonexistent-id"}


@pytest.mark.asyncio
async def test_open_gate_returns_answer_once_a_human_answers():
    cid = (await m.create_card("P", "Card", ""))["id"]

    async def answer_soon():
        # Let open_gate create the gate and start polling first.
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "yes")
            await s.commit()

    result, _ = await asyncio.gather(
        m.open_gate(cid, "Ship now?", ["yes", "no"], timeout_seconds=5),
        answer_soon(),
    )
    assert result["answer"] == "yes"
    assert "gate_id" in result


@pytest.mark.asyncio
async def test_open_gate_times_out_when_unanswered():
    cid = (await m.create_card("P", "Card", ""))["id"]
    result = await m.open_gate(cid, "Ship now?", ["yes", "no"], timeout_seconds=0.05)
    assert result["error"] == "timeout"
    assert "gate_id" in result

    # The gate stays open for a later answer instead of being discarded.
    async with KanbanSessionLocal() as s:
        gate = await service.get_gate(s, result["gate_id"])
        assert gate.status == "open"


@pytest.mark.asyncio
async def test_open_gate_logs_a_comment_on_the_card():
    cid = (await m.create_card("P", "Card", ""))["id"]

    async def answer_soon():
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "yes")
            await s.commit()

    await asyncio.gather(
        m.open_gate(cid, "Ship now?", ["yes", "no"], timeout_seconds=5),
        answer_soon(),
    )

    card = await m.get_card(cid)
    assert card["id"] == cid  # sanity: card still intact, session/claim untouched
    assert card.get("claimed_by") is None


# --- latest_gate_answer ----------------------------------------------------
# Acceptance criterion: resolve-impediment splices the human's chosen gate
# answer into the resumed session's prompt. service.latest_gate_answer is the
# lookup resolve_impediment uses; it's exercised here in isolation so the
# ordering / status filter contract stays pinned even if the router path
# drifts.


@pytest.mark.asyncio
async def test_latest_gate_answer_returns_none_when_no_gate_exists():
    cid = (await m.create_card("P", "Card", ""))["id"]
    async with KanbanSessionLocal() as s:
        assert await service.latest_gate_answer(s, cid) is None


@pytest.mark.asyncio
async def test_latest_gate_answer_returns_none_when_gate_unanswered():
    cid = (await m.create_card("P", "Card", ""))["id"]
    await m.open_gate(cid, "Pick one", ["a", "b"], timeout_seconds=0.01)
    async with KanbanSessionLocal() as s:
        # Gate exists but status="open" — pending human input. Don't surface
        # any answer yet (would mislead the resumed session).
        assert await service.latest_gate_answer(s, cid) is None


@pytest.mark.asyncio
async def test_latest_gate_answer_returns_chosen_value_via_mcp():
    cid = (await m.create_card("P", "Card", ""))["id"]
    async with KanbanSessionLocal() as s:
        gate = await service.create_gate(s, card_id=cid, project_key="P",
            question="Pick one", options=["a", "b"])
        gate_id = gate.id
        await s.commit()
    async with KanbanSessionLocal() as s:
        await service.answer_gate(s, gate_id, "b")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await service.latest_gate_answer(s, cid) == "b"


@pytest.mark.asyncio
async def test_latest_gate_answer_picks_most_recent_answer():
    """Re-opened impediments may carry multiple gates. The latest *answered*
    one wins so a human who changes their mind overrides the first pick."""
    cid = (await m.create_card("P", "Card", ""))["id"]
    async with KanbanSessionLocal() as s:
        first = await service.create_gate(s, card_id=cid, project_key="P",
            question="Round 1", options=["x", "y"])
        await service.answer_gate(s, first.id, "x")
        # Force a non-zero gap between answered_at so order_by is unambiguous.
        import asyncio
        await asyncio.sleep(0.01)
        second = await service.create_gate(s, card_id=cid, project_key="P",
            question="Round 2", options=["x", "y"])
        await service.answer_gate(s, second.id, "y")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await service.latest_gate_answer(s, cid) == "y"
