# backend/tests/test_kanban_resolve_impediment.py
"""Resolve-impediment: the human's chosen gate answer must be forwarded as
`impediment_answer` (separate from `impediment_question`) so `build_card_prompt`
can render the `## IMPEDIMENT` prompt section with both the original ask AND
the human's pick. Mirrors the existing test_kanban_impediment_answer.py where
the free-text `**Resolution:**` comment is forwarded via the same channel.

We mock `app.kanban.dispatch.dispatch_impediment_card` so the test stays a
pure orchestration check (we assert what fields are forwarded) and never
touches tmux / claude CLI / worktrees. The conftest auto-patches
`KanbanSessionLocal` so the router hits the test DB.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import mcp_server as m
from app.kanban import service
from app.main import app
from tests.kanban_test_db import TestSessionLocal

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_resolve_impediment_forwards_gate_answer_as_impediment_answer(
        monkeypatch, _client):
    """End-to-end on the REST path: report_impediment with options= opens a
    gate, the human answers it, resolve-impediment must forward BOTH the
    original `**Impediment:**` question and the chosen option — as separate
    fields, the way `build_card_prompt` renders them."""
    from app.kanban import dispatch as dispatch_mod

    captured: dict = {}

    async def fake_dispatch(s, *, card_id, project_path, target_agent,
                            impediment_question, impediment_answer=None,
                            transport=None):
        captured["card_id"] = card_id
        captured["impediment_question"] = impediment_question
        captured["impediment_answer"] = impediment_answer
        return {"session_name": "fake"}

    monkeypatch.setattr(dispatch_mod, "dispatch_impediment_card", fake_dispatch)

    # 1. Create card + report_impediment with options (mirrors the new MCP path).
    card = await m.create_card("P", "blocked", "details", agent="engineer", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:sess")
    await m.report_impediment(cid, "Postgres or SQLite?",
                              options=["Postgres", "SQLite", "MySQL", "MariaDB"])

    # 2. Human answers the gate (mirrors the UI clicking a choice).
    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
        gate_id = gates[0].id
        await service.answer_gate(s, gate_id, "Postgres")
        await s.commit()

    # 3. Resolve-impediment must compose question + chosen option into the
    #    dispatched session's prompt — as separate fields.
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp", "target_agent": "engineer"},
    )
    assert r.status_code == 200, r.text
    assert captured["impediment_question"] == "Postgres or SQLite?"
    assert captured["impediment_answer"] == "Postgres"


@pytest.mark.asyncio
async def test_resolve_impediment_legacy_free_text_question_path(
        monkeypatch, _client):
    """Backwards compat: a card in Impediment with NO gate (the legacy free-text
    path) must still work — the raw `**Impediment:**` question is forwarded
    and `impediment_answer` is None."""
    from app.kanban import dispatch as dispatch_mod

    captured: dict = {}

    async def fake_dispatch(s, *, card_id, project_path, target_agent,
                            impediment_question, impediment_answer=None,
                            transport=None):
        captured["impediment_question"] = impediment_question
        captured["impediment_answer"] = impediment_answer
        return {"session_name": "fake"}

    monkeypatch.setattr(dispatch_mod, "dispatch_impediment_card", fake_dispatch)

    card = await m.create_card("P", "blocked", "details", agent="engineer", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:sess")
    # Legacy call — no options. No KanbanGate is created.
    await m.report_impediment(cid, "I need a schema review.")

    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp", "target_agent": "engineer"},
    )
    assert r.status_code == 200
    assert captured["impediment_question"] == "I need a schema review."
    assert captured["impediment_answer"] is None


@pytest.mark.asyncio
async def test_resolve_impediment_gate_leads_and_free_text_follows(
        monkeypatch, _client):
    """Priority rule: when both a gate answer AND free text exist on the same
    card, the gate's structured pick is the *decision* and the free text is
    supporting context — both reach the session, with the choice labelled and
    listed first (kaart c3419f63, fix option 3).

    Rationale for carrying both rather than dropping the text: once a gate is
    answered, the UI labels the textarea "Optional: add extra context for the
    resumed session" (kaart 4279448c), so the text is context by design. The
    earlier contract discarded it, which silently lost operator input."""
    from app.kanban import dispatch as dispatch_mod
    from app.kanban import dispatch as dispatch_module

    captured: dict = {}

    async def fake_dispatch(s, *, card_id, project_path, target_agent,
                            impediment_question, impediment_answer=None,
                            transport=None):
        captured["impediment_question"] = impediment_question
        captured["impediment_answer"] = impediment_answer
        return {"session_name": "fake"}

    monkeypatch.setattr(dispatch_mod, "dispatch_impediment_card", fake_dispatch)

    # 1. Create card + report_impediment with options, then answer the gate.
    card = await m.create_card("P", "blocked", "details", agent="engineer", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:sess")
    await m.report_impediment(cid, "Postgres or SQLite?",
                              options=["Postgres", "SQLite", "MySQL", "MariaDB"])
    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
        await service.answer_gate(s, gates[0].id, "Postgres")
        await s.commit()

    # 2. Resolve-impediment with extra context in `answer` — the structured
    #    gate pick leads, the typed context follows it.
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp", "target_agent": "engineer",
              "answer": "mind the connection pool"},
    )
    assert r.status_code == 200, r.text
    assert captured["impediment_question"] == "Postgres or SQLite?"
    answer = captured["impediment_answer"]
    assert "Postgres" in answer
    assert "mind the connection pool" in answer
    assert answer.index("Postgres") < answer.index("mind the connection pool")
    _ = dispatch_module  # silence unused-import lint (we only need dispatch_mod)


@pytest.mark.asyncio
async def test_resolve_impediment_closes_open_gate_when_human_uses_free_text(
        monkeypatch, _client):
    """Regression for kaart 504b4e8a…: when ``report_impediment`` opened a
    KanbanGate (options=) and the human *only* typed free text — without
    clicking one of the choice buttons — the gate stayed ``status='open'``
    forever. The card would then linger on ``po_wachtrij`` (gate_open
    emission ignores column + Done filter) and ``CardDrawer`` would render a
    stale "Decision requested" paneel after the card moved to Backlog.

    ``resolve_impediment`` is the single source of truth for "impediment
    resolved"; it must close every open gate on the card and record the
    free-text as the gate's answer (so ``service.latest_gate_answer`` keeps
    forwarding it to the resumed session — same channel a structured pick
    uses). The frontend (``ImpedimentPage.submit``) deliberately does NOT
    call ``answerGate`` when the user picks no choice button, so the fix has
    to live here.
    """
    from app.kanban import dispatch as dispatch_mod

    async def fake_dispatch(s, *, card_id, project_path, target_agent,
                            impediment_question, impediment_answer=None,
                            transport=None):
        return {"session_name": "fake"}

    monkeypatch.setattr(dispatch_mod, "dispatch_impediment_card", fake_dispatch)

    # 1. Create card + report_impediment WITH options (gate gets created).
    card = await m.create_card("P", "freetext-resolve", "details",
                               agent="engineer", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:sess")
    await m.report_impediment(cid, "Postgres or SQLite?",
                              options=["Postgres", "SQLite", "MySQL", "MariaDB"])

    # 2. Human does NOT click a choice button — only types free-text and
    #    submits resolve-impediment (the bug scenario).
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/resolve-impediment",
        json={"project_path": "/tmp", "target_agent": "engineer",
              "answer": "stay on Postgres for now, can revisit later"},
    )
    assert r.status_code == 200, r.text

    # 3. The open gate must be closed — status=answered, answer recorded.
    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
    assert len(gates) == 1
    gate = gates[0]
    assert gate.status == "answered", (
        f"gate must be closed after free-text resolve, got status={gate.status!r}"
    )
    assert gate.answer == "stay on Postgres for now, can revisit later"
    assert gate.answered_at is not None

    # 4. The card must NOT linger in po_wachtrij (was the second visible
    #    consequence — gate_open surfaced for any column, even Done).
    async with KanbanSessionLocal() as s:
        items = await service.po_wachtrij(s, "P")
    assert items == [], (
        f"resolved-with-free-text card must not stay in po_wachtrij; got {items!r}"
    )

    # 5. The free-text must also reach the resumed session via
    #    ``service.latest_gate_answer`` so the same channel a structured
    #    pick uses carries it (kaart c3419f63).
    async with KanbanSessionLocal() as s:
        latest = await service.latest_gate_answer(s, cid)
    assert latest == "stay on Postgres for now, can revisit later"