# backend/tests/test_kanban_impediment_gate_handoff.py
"""A structured gate choice must reach the *resumed* session, not just the
router.

`router.resolve_impediment` parks the card on Backlog; the actual spawn happens
on the next auto-tick, which re-reads the impediment context via
`dispatch._resolve_impediment`. That reader used to look only at the latest
`**Resolution:**` comment — and `service.answer_gate` posts no such comment —
so an operator who clicked a choice button and typed nothing saw their decision
silently dropped (kanban card c3419f63). These tests pin the gate-aware
fallback and the combined "choice + extra context" rendering.
"""
import pytest
import pytest_asyncio

from app.kanban import dispatch, service
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

IMPEDIMENT_PREFIX = "**Impediment:** "
RESOLUTION_PREFIX = "**Resolution:** "


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_impediment_card(s, *, project_key="P",
                                question="Postgres or SQLite?"):
    cid = await apply_operation(s, op_type="create", entity_type="card",
        project_key=project_key, entity_id=None, payload={"title": "blocked"})
    await apply_operation(s, op_type="move", entity_type="card",
        project_key="", entity_id=cid, payload={"column": "Impediment"})
    await apply_operation(s, op_type="comment", entity_type="comment",
        project_key="", entity_id=cid,
        payload={"text": f"{IMPEDIMENT_PREFIX}{question}"})
    await s.commit()
    return cid


async def _answered_gate(s, cid, *, project_key="P",
                         question="Postgres or SQLite?",
                         options=("Postgres", "SQLite"), answer="Postgres"):
    gate = await service.create_gate(s, cid, project_key, question, list(options))
    await service.answer_gate(s, gate.id, answer)
    await s.commit()
    return gate


# --- compose helper ----------------------------------------------------------


def test_compose_prefers_gate_and_keeps_extra_context():
    """Both present: the gate choice is the decision, the free text is context."""
    composed = dispatch.compose_impediment_answer("Postgres", "watch the pool size")
    assert composed is not None
    assert "Postgres" in composed
    assert "watch the pool size" in composed
    # The choice must be labelled so the agent can tell decision from context.
    assert composed.index("Postgres") < composed.index("watch the pool size")


def test_compose_gate_only():
    assert dispatch.compose_impediment_answer("Postgres", None) == "Postgres"


def test_compose_free_text_only():
    assert dispatch.compose_impediment_answer(None, "Use library B.") == "Use library B."


def test_compose_none_when_neither():
    assert dispatch.compose_impediment_answer(None, None) is None


def test_compose_deduplicates_identical_values():
    """The operator typed exactly what they clicked — don't echo it twice."""
    assert dispatch.compose_impediment_answer("Postgres", "Postgres") == "Postgres"


# --- _resolve_impediment (the auto-tick reader) -------------------------------


@pytest.mark.asyncio
async def test_resolve_impediment_falls_back_to_gate_answer():
    """The regression: gate answered, no `**Resolution:**` comment at all."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        await _answered_gate(s, cid)
        card = await service.get_card(s, cid)
        question, answer = await dispatch._resolve_impediment(s, card)

    assert question == "Postgres or SQLite?"
    assert answer == "Postgres"


@pytest.mark.asyncio
async def test_resolve_impediment_combines_gate_and_resolution_comment():
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        await _answered_gate(s, cid)
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"{RESOLUTION_PREFIX}mind the connection pool"})
        await s.commit()
        card = await service.get_card(s, cid)
        _, answer = await dispatch._resolve_impediment(s, card)

    assert answer is not None
    assert "Postgres" in answer
    assert "mind the connection pool" in answer


@pytest.mark.asyncio
async def test_resolve_impediment_free_text_only_unchanged():
    """No gate on the card: the legacy `**Resolution:**` path is untouched."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s, question="Which library?")
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid,
            payload={"text": f"{RESOLUTION_PREFIX}Use library B."})
        await s.commit()
        card = await service.get_card(s, cid)
        question, answer = await dispatch._resolve_impediment(s, card)

    assert question == "Which library?"
    assert answer == "Use library B."


@pytest.mark.asyncio
async def test_resolve_impediment_ignores_gate_without_impediment_question():
    """An ordinary card that happens to carry an answered gate (e.g. from
    `open_gate`) must not grow a phantom `## IMPEDIMENT` answer."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="P", entity_id=None, payload={"title": "ordinary"})
        await _answered_gate(s, cid, question="ship now?",
                             options=("yes", "no"), answer="yes")
        card = await service.get_card(s, cid)
        question, answer = await dispatch._resolve_impediment(s, card)

    assert question is None
    assert answer is None


@pytest.mark.asyncio
async def test_resolve_impediment_ignores_open_gate():
    """An unanswered gate is not a decision — the prompt keeps the
    'please address this question' framing."""
    async with KanbanSessionLocal() as s:
        cid = await _make_impediment_card(s)
        await service.create_gate(s, cid, "P", "Postgres or SQLite?",
                                  ["Postgres", "SQLite"])
        await s.commit()
        card = await service.get_card(s, cid)
        _, answer = await dispatch._resolve_impediment(s, card)

    assert answer is None


# --- prompt rendering --------------------------------------------------------


def test_build_card_prompt_blockquotes_every_line_of_a_multiline_answer():
    """A composed answer spans multiple lines; each must stay inside the
    blockquote or the second line reads as loose prose in the prompt."""
    class _C:
        title = "Bug"
        description = "Fix the crash"

    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        impediment_question="Postgres or SQLite?",
        impediment_answer="Chosen option: Postgres\n\nExtra context: pool size",
    )
    assert "> Chosen option: Postgres" in prompt
    assert "> Extra context: pool size" in prompt
