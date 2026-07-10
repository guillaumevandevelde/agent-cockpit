# backend/tests/test_kanban_impediment_answer.py
"""A human's answer to an impediment reaches the next dispatched session.

When `report_impediment` parks a card in the Impediment column it posts a
`**Impediment:** <question>` comment and releases the claim. Previously a
human's answer had no reliable path back into the resumed session's prompt.
This wires that channel: `/resolve-impediment` accepts an `answer`, stamps it
as a durable `**Resolution:** <answer>` comment, and injects it into the
resumed session's `## IMPEDIMENT` prompt section (mirror of the `## REVISIT`
reopen flow).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch, service
from app.kanban.operations import apply_operation
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

IMPEDIMENT_PREFIX = "**Impediment:** "
RESOLUTION_PREFIX = "**Resolution:** "


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


async def _make_impediment_card(s, project_key="P", title="Fix the crash",
                                question="Which library should we use?"):
    """Create a card, park it in Impediment with an `**Impediment:**` comment."""
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload={"title": title})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Impediment"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid,
        payload={"text": f"{IMPEDIMENT_PREFIX}{question}"})
    await s.commit()
    return cid


# --- extraction --------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_impediment_answer_picks_latest_resolution():
    """Multiple resolve rounds: the newest `**Resolution:**` comment wins."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"{RESOLUTION_PREFIX}Use library A."})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"{RESOLUTION_PREFIX}On reflection, use library B."})
        await s.commit()
        activity = await service.card_activity(s, cid)

    assert dispatch.extract_impediment_answer(activity) == "On reflection, use library B."


@pytest.mark.asyncio
async def test_extract_impediment_answer_none_without_resolution():
    """A card with only the impediment question yields no answer."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        activity = await service.card_activity(s, cid)
    assert dispatch.extract_impediment_answer(activity) is None


# --- prompt ------------------------------------------------------------------


def test_build_card_prompt_renders_impediment_answer_when_present():
    class _C:
        title = "Bug"
        description = "Fix the crash"
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        impediment_question="Which library?",
        impediment_answer="Use library B.",
    )
    assert "## IMPEDIMENT" in prompt
    assert "Which library?" in prompt
    assert "Use library B." in prompt
    assert "authoritative" in prompt


def test_build_card_prompt_omits_answer_block_when_no_answer():
    class _C:
        title = "Bug"
        description = "Fix the crash"
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        impediment_question="Which library?",
    )
    assert "## IMPEDIMENT" in prompt
    assert "Which library?" in prompt
    assert "authoritative" not in prompt
    assert "clarify what's needed" in prompt


def test_build_card_prompt_documents_rest_fallback_for_minus_32602():
    """Every dispatched session must carry the REST fallback so an intermittent
    `-32602` MCP handshake race doesn't strand a finished card in its dispatch
    column (kanban card 7b1d0a91: MCP tools returned -32602 on every call)."""
    class _C:
        title = "Bug"
        description = "Fix the crash"
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
    assert "-32602" in prompt
    assert "/api/v1/kanban" in prompt
    # The concrete endpoints an agent needs to move/comment/attach without MCP.
    assert "/cards/{id}/move" in prompt
    assert "/cards/{id}/comment" in prompt
    assert "/cards/{id}/deliverables" in prompt


# --- REST --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_resolve_impediment_stamps_resolution_and_threads_answer(
    _client, monkeypatch,
):
    """The human's `answer` is posted as a durable `**Resolution:**` comment and
    passed to the dispatcher as `impediment_answer`."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s, project_key="REST")

    captured = {}

    async def _fake_dispatch(session, *, card_id, project_path, target_agent,
                             impediment_question, impediment_answer=None,
                             transport=None):
        captured["question"] = impediment_question
        captured["answer"] = impediment_answer
        return {"session_name": "sess-1"}

    monkeypatch.setattr(dispatch, "dispatch_impediment_card", _fake_dispatch)

    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp/x", "answer": "Use library B."},
    )
    assert r.status_code == 200, r.text

    # The answer threaded through to the dispatcher.
    assert captured["answer"] == "Use library B."
    assert captured["question"] == "Which library should we use?"

    # ...and it's auditable as a durable Resolution comment.
    act = await _client.get(f"/api/v1/kanban/cards/{cid}/activity")
    texts = [e["payload"].get("text", "") for e in act.json()
             if e["op_type"] == "comment"]
    assert any(t.startswith(RESOLUTION_PREFIX) and "Use library B." in t
               for t in texts)


@pytest.mark.asyncio
async def test_rest_resolve_impediment_without_answer_threads_none(
    _client, monkeypatch,
):
    """Omitting `answer` keeps the legacy behaviour: no Resolution comment,
    impediment_answer is None."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s, project_key="REST")

    captured = {}

    async def _fake_dispatch(session, *, card_id, project_path, target_agent,
                             impediment_question, impediment_answer=None,
                             transport=None):
        captured["answer"] = impediment_answer
        return {"session_name": "sess-1"}

    monkeypatch.setattr(dispatch, "dispatch_impediment_card", _fake_dispatch)

    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp/x"},
    )
    assert r.status_code == 200, r.text
    assert captured["answer"] is None

    act = await _client.get(f"/api/v1/kanban/cards/{cid}/activity")
    texts = [e["payload"].get("text", "") for e in act.json()
             if e["op_type"] == "comment"]
    assert not any(t.startswith(RESOLUTION_PREFIX) for t in texts)


@pytest.mark.asyncio
async def test_rest_resolve_impediment_422_when_not_in_impediment(_client):
    r = await _client.post("/api/v1/kanban/cards",
        json={"project_key": "REST", "title": "not blocked"})
    cid = r.json()["id"]
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp/x", "answer": "hi"},
    )
    assert r.status_code == 422, r.text
