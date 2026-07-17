# backend/tests/test_kanban_gates.py
"""Decision gates: data model, service layer, and REST endpoints."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(project_key="P", title="t") -> str:
    from app.kanban.operations import apply_operation
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=None, payload={"title": title})
        await s.commit()
        return cid


# --- service layer ---


@pytest.mark.asyncio
async def test_create_gate_defaults_to_open():
    cid = await _make_card()
    async with KanbanSessionLocal() as s:
        gate = await service.create_gate(s, card_id=cid, project_key="P",
            question="Ship now?", options=["yes", "no"])
        await s.commit()
        assert gate.status == "open"
        assert gate.answer is None
        assert gate.options == ["yes", "no"]


@pytest.mark.asyncio
async def test_list_gates_for_card():
    cid = await _make_card()
    async with KanbanSessionLocal() as s:
        await service.create_gate(s, card_id=cid, project_key="P",
            question="A?", options=["x"])
        await service.create_gate(s, card_id=cid, project_key="P",
            question="B?", options=["y"])
        await s.commit()

    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
        assert [g.question for g in gates] == ["A?", "B?"]


@pytest.mark.asyncio
async def test_answer_gate_sets_status_and_answer():
    cid = await _make_card()
    async with KanbanSessionLocal() as s:
        gate = await service.create_gate(s, card_id=cid, project_key="P",
            question="Ship now?", options=["yes", "no"])
        await s.commit()
        gate_id = gate.id

    async with KanbanSessionLocal() as s:
        answered = await service.answer_gate(s, gate_id, "yes")
        await s.commit()
        assert answered.status == "answered"
        assert answered.answer == "yes"
        assert answered.answered_at is not None


@pytest.mark.asyncio
async def test_answer_gate_rejects_invalid_option():
    cid = await _make_card()
    async with KanbanSessionLocal() as s:
        gate = await service.create_gate(s, card_id=cid, project_key="P",
            question="Ship now?", options=["yes", "no"])
        await s.commit()
        gate_id = gate.id

    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await service.answer_gate(s, gate_id, "maybe")


@pytest.mark.asyncio
async def test_answer_gate_idempotent_second_answer_ignored():
    cid = await _make_card()
    async with KanbanSessionLocal() as s:
        gate = await service.create_gate(s, card_id=cid, project_key="P",
            question="Ship now?", options=["yes", "no"])
        await s.commit()
        gate_id = gate.id

    async with KanbanSessionLocal() as s:
        await service.answer_gate(s, gate_id, "yes")
        await s.commit()

    async with KanbanSessionLocal() as s:
        second = await service.answer_gate(s, gate_id, "no")
        await s.commit()
        assert second.answer == "yes"  # first answer wins, no-op on re-answer


@pytest.mark.asyncio
async def test_answer_gate_not_found():
    async with KanbanSessionLocal() as s:
        result = await service.answer_gate(s, "nonexistent", "yes")
        assert result is None


# --- REST endpoints ---


@pytest.mark.asyncio
async def test_open_gate_via_api_creates_gate_and_activity_comment():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Card", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/gates",
            json={"question": "Which approach?", "options": ["A", "B"]})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "open"
        assert body["options"] == ["A", "B"]

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/activity")
        texts = [e["payload"].get("text", "") for e in r.json() if e["op_type"] == "comment"]
        assert any("Which approach?" in t for t in texts)


@pytest.mark.asyncio
async def test_open_gate_card_not_found_404():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards/nonexistent/gates",
            json={"question": "Q?", "options": ["A"]})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_gates_via_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Card", "confirm_new_project": True})
        cid = r.json()["id"]
        await ac.post(f"/api/v1/kanban/cards/{cid}/gates",
            json={"question": "Q1?", "options": ["A"]})
        await ac.post(f"/api/v1/kanban/cards/{cid}/gates",
            json={"question": "Q2?", "options": ["B"]})

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/gates")
        assert r.status_code == 200
        assert [g["question"] for g in r.json()] == ["Q1?", "Q2?"]


@pytest.mark.asyncio
async def test_answer_gate_via_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Card", "confirm_new_project": True})
        cid = r.json()["id"]
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/gates",
            json={"question": "Ship?", "options": ["yes", "no"]})
        gate_id = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/gates/{gate_id}/answer",
            json={"answer": "yes"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "answered"
        assert r.json()["answer"] == "yes"


@pytest.mark.asyncio
async def test_answer_gate_invalid_option_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Card", "confirm_new_project": True})
        cid = r.json()["id"]
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/gates",
            json={"question": "Ship?", "options": ["yes", "no"]})
        gate_id = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/gates/{gate_id}/answer",
            json={"answer": "maybe"})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_answer_gate_not_found_404():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/gates/nonexistent/answer",
            json={"answer": "yes"})
        assert r.status_code == 404
