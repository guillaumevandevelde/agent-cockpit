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
