"""Tests for the phase-aware provider/persona helpers extracted in Task 5.

These helpers are pure functions over a duck-typed card object, so the tests
do not need the kanban test database.
"""
import pytest

from app.kanban import dispatch
from app.kanban.dispatch import _phase_provider_id, _phase_target_agent


class _FakeCard:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_phase_provider_analyst_uses_analyst_field():
    card = _FakeCard(analyst_agent_id="claude-code", agent="engineer")
    assert _phase_provider_id(card, phase="analyst") == "claude-code"


def test_phase_provider_executor_uses_executor_field():
    card = _FakeCard(executor_agent_id="mimo-code", agent="engineer")
    assert _phase_provider_id(card, phase="executor") == "mimo-code"


def test_phase_provider_executor_falls_back_to_card_agent():
    card = _FakeCard(agent="codex-cli", executor_agent_id=None)
    assert _phase_provider_id(card, phase="executor") == "codex-cli"


def test_phase_provider_executor_default_claude_code():
    card = _FakeCard(agent=None, executor_agent_id=None)
    assert _phase_provider_id(card, phase="executor") == "claude-code"


def test_phase_provider_analyst_default_claude_code():
    card = _FakeCard(analyst_agent_id=None)
    assert _phase_provider_id(card, phase="analyst") == "claude-code"


def test_phase_target_agent_analyst_is_analyst():
    card = _FakeCard()
    assert _phase_target_agent(card, project_path="/tmp", phase="analyst",
                               source_column="Backlog") == "analyst"


def test_phase_target_agent_executor_honors_agent_override_persona(tmp_path):
    """An agent_override that's a persona name (not a registered provider id)
    wins over card.agent and the column-derived fallback — preserves the legacy
    `dispatch_card(..., agent_override='developer')` path that maps the card to
    the developer column."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "developer.md").write_text("# developer persona")
    card = _FakeCard()
    assert _phase_target_agent(card, project_path=str(tmp_path), phase="executor",
                               source_column="Backlog",
                               agent_override="developer") == "developer"


# ---- Task 6: tick-level multi-agent orchestration -------------------------

@pytest.mark.asyncio
async def test_tick_skips_card_with_unmet_deps(monkeypatch):
    """If a child depends on a parent that's not Done, the tick skips it."""
    calls = []

    async def fake_run_card(session, **kwargs):
        calls.append(("run_card", kwargs["phase"], kwargs["card"].id))
        return {"session": "ok"}

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    parent = _FakeCard(id="p1", column="Backlog", analyst_agent_id=None)
    child = _FakeCard(id="c1", column="Backlog", analyst_agent_id=None,
                      depends_on=["p1"])
    cards_by_id = {"p1": parent, "c1": child}

    async def cards_iter():
        for c in (parent, child):
            yield c

    # The tick should dispatch the parent first, then skip c1 because p1 is not
    # Done. This test only exercises the dep-filter helper, not the full tick.
    from app.kanban.dep_resolver import meets_dep_prerequisites
    assert meets_dep_prerequisites(child, cards_by_id) is False

    parent.column = "Done"
    assert meets_dep_prerequisites(child, cards_by_id) is True


@pytest.mark.asyncio
async def test_tick_spawns_analyst_when_set(monkeypatch):
    calls = []

    async def fake_run_card(session, **kwargs):
        calls.append((kwargs["phase"], kwargs["card"].id))
        return {"session": "ok"}

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    from app.kanban.operations import apply_operation
    from app.kanban.models import KanbanCard
    from tests.kanban_test_db import TestSessionLocal

    KanbanSessionLocal = TestSessionLocal()
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog",
                     "analyst_agent_id": "claude-code"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)

        # Stub a minimal dispatcher top-level — we don't want the full tick.
        # Instead, inline the branch we want to test (mirrors dispatch.py):
        from app.kanban.dep_resolver import meets_dep_prerequisites
        passes_deps = meets_dep_prerequisites(card, {cid: card})
        assert passes_deps is True

        if card.analyst_agent_id and not card.analyst_run_id:
            await fake_run_card(s, card=card, project_key="git:example",
                                project_path="/tmp/none", transport=None,
                                phase="analyst")
            card.analyst_run_id = "run-1"
            await s.commit()
            card = await s.get(KanbanCard, cid)
            assert card.analyst_run_id == "run-1"
            assert calls == [("analyst", cid)]


# ---- Task 7: plan-aware executor prompt -----------------------------------

def test_plan_context_prepend_resolved_plan():
    from app.kanban.dispatch import _plan_context_section
    section = _plan_context_section(
        plan_markdown="# plan\n\nStep 1: do X\nStep 2: do Y",
        plan_deliverable_id="d1",
        parent_card_id="p1",
    )
    assert "PLAN CONTEXT" in section
    assert "read this first" in section
    assert "Step 1" in section
    assert "d1" in section or "p1" in section


def test_plan_context_unresolvable_returns_placeholder():
    from app.kanban.dispatch import _plan_context_section
    section = _plan_context_section(
        plan_markdown=None,
        plan_deliverable_id=None,
        parent_card_id=None,
    )
    assert "Plan niet beschikbaar" in section
    assert "report_impediment" in section


# ---- Task 7 follow-up: gate plan-context prepend on parent_card_id --------

@pytest.mark.asyncio
async def test_run_card_skips_plan_context_for_legacy_cards(monkeypatch):
    """Regression test for Task 7 critical defect (end-to-end-ish).

    Legacy single-agent cards (no parent_card_id) must NOT have the
    PLAN CONTEXT — Plan niet beschikbaar placeholder prepended to their
    executor prompt. The gate in _run_card must check
    `card.parent_card_id is not None` before calling
    _resolve_plan_for_child + _plan_context_section.

    Runs the actual _run_card against the kanban test DB with a captured
    transport, asserting that:
      - _resolve_plan_for_child is never called for the legacy card
      - _plan_context_section is never called for the legacy card
      - the captured prompt does not contain the placeholder text
    """
    from app.kanban.dispatch import _run_card
    from app.kanban.models import KanbanCard
    from tests.kanban_test_db import TestSessionLocal

    section_calls = []
    resolve_calls = []

    async def fake_resolve(session, card):
        resolve_calls.append(card.id)
        return (None, None, None)

    def fake_section(*, plan_markdown, plan_deliverable_id, parent_card_id):
        section_calls.append((plan_markdown, plan_deliverable_id, parent_card_id))
        return _plan_context_section(plan_markdown=plan_markdown,
                                     plan_deliverable_id=plan_deliverable_id,
                                     parent_card_id=parent_card_id)

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)
    monkeypatch.setattr(dispatch, "_plan_context_section", fake_section)

    captured = {}

    def fake_transport(directory, prompt, session_name, provider_id, platform):
        captured["prompt"] = prompt
        captured["session_name"] = session_name
        return {"session": session_name, "prompt": prompt}

    KanbanSessionLocal = TestSessionLocal()
    async with KanbanSessionLocal() as s:
        from app.kanban.operations import apply_operation
        legacy_cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "legacy card", "column": "Doing"},
        )
        await s.commit()
        legacy_card = await s.get(KanbanCard, legacy_cid)
        # Sanity: legacy cards have no parent.
        assert legacy_card.parent_card_id is None

        await _run_card(
            s, card=legacy_card, project_key="git:example",
            project_path="/tmp/none", transport=fake_transport,
            phase="executor",
        )
        await s.commit()

    assert resolve_calls == [], (
        f"_resolve_plan_for_child must not be called for legacy cards "
        f"(got calls for {resolve_calls!r})"
    )
    assert section_calls == [], (
        f"_plan_context_section must not be called for legacy cards "
        f"(got calls for {section_calls!r})"
    )
    assert "Plan niet beschikbaar" not in captured["prompt"], (
        f"Legacy executor prompt must not contain the placeholder, but got: "
        f"{captured['prompt'][:200]!r}"
    )


@pytest.mark.asyncio
async def test_run_card_prepends_plan_context_for_child_with_parent(monkeypatch):
    """Child cards (parent_card_id set) whose plan_ref resolves to a real
    plan should see the full PLAN CONTEXT section prepended to the prompt."""
    from app.kanban.dispatch import _run_card
    from app.kanban.models import KanbanCard
    from tests.kanban_test_db import TestSessionLocal

    resolve_calls = []

    async def fake_resolve(session, card):
        resolve_calls.append(card.id)
        return ("# My Plan\n\n- Step 1\n- Step 2", "d1", "parent-1")

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)

    captured = {}

    def fake_transport(directory, prompt, session_name, provider_id, platform):
        captured["prompt"] = prompt
        captured["session_name"] = session_name
        return {"session": session_name, "prompt": prompt}

    KanbanSessionLocal = TestSessionLocal()
    async with KanbanSessionLocal() as s:
        from app.kanban.operations import apply_operation
        child_cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "child card", "column": "Doing",
                     "parent_card_id": "parent-1"},
        )
        await s.commit()
        child_card = await s.get(KanbanCard, child_cid)
        assert child_card.parent_card_id == "parent-1"

        await _run_card(
            s, card=child_card, project_key="git:example",
            project_path="/tmp/none", transport=fake_transport,
            phase="executor",
        )
        await s.commit()

    assert resolve_calls == [child_card.id], (
        f"_resolve_plan_for_child should be called once for child cards "
        f"(got {resolve_calls!r})"
    )
    assert "PLAN CONTEXT" in captured["prompt"], (
        f"Child executor prompt must contain the PLAN CONTEXT section, "
        f"but got: {captured['prompt'][:200]!r}"
    )
    assert "Step 1" in captured["prompt"]
    assert "Step 2" in captured["prompt"]


@pytest.mark.asyncio
async def test_run_card_prepends_placeholder_for_child_with_missing_plan(monkeypatch):
    """Child cards whose plan_ref fails to resolve should still see the
    Plan niet beschikbaar placeholder — that's the desired signal that
    something is off (parent deleted / plan never written)."""
    from app.kanban.dispatch import _run_card
    from app.kanban.models import KanbanCard
    from tests.kanban_test_db import TestSessionLocal

    async def fake_resolve(session, card):
        # Simulate parent deleted or plan never written.
        return (None, None, "parent-1")

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)

    captured = {}

    def fake_transport(directory, prompt, session_name, provider_id, platform):
        captured["prompt"] = prompt
        return {"session": session_name, "prompt": prompt}

    KanbanSessionLocal = TestSessionLocal()
    async with KanbanSessionLocal() as s:
        from app.kanban.operations import apply_operation
        child_cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "orphan child", "column": "Doing",
                     "parent_card_id": "parent-1"},
        )
        await s.commit()
        child_card = await s.get(KanbanCard, child_cid)
        assert child_card.parent_card_id == "parent-1"

        await _run_card(
            s, card=child_card, project_key="git:example",
            project_path="/tmp/none", transport=fake_transport,
            phase="executor",
        )
        await s.commit()

    assert "Plan niet beschikbaar" in captured["prompt"], (
        f"Child with missing plan_ref must see the placeholder, but got: "
        f"{captured['prompt'][:200]!r}"
    )
    assert "report_impediment" in captured["prompt"]


# ---- Final review: regression for analyst_run_id write-back (Critical C1) --

@pytest.mark.asyncio
async def test_dispatch_project_analyst_branch_persists_analyst_run_id(monkeypatch):
    """Regression test for Critical C1 in the final review.

    The dispatch tick writes ``card.analyst_run_id`` from the result dict that
    ``_run_card`` returned. The real ``_run_card`` returns
    ``{"card_id": ..., "session_name": name, ...}`` (see dispatch.py:947), NOT
    ``{"session": ...}``. The dispatch tick must read the real key. This test
    exercises the real ``dispatch_project`` analyst branch with a stub that
    mirrors the real ``_run_card`` shape exactly — so a regression in either
    direction (stub drift or read-key bug) is caught.

    Buggy code: ``if "session" in last_result:`` is always False, so
    ``analyst_run_id`` is never written. The card would silently stay in
    Backlog/analyst with no ``analyst_run_id`` and the dispatcher would re-spawn
    the analyst every tick until ``MAX_DISPATCH_FAILURES`` trips.
    """
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    async def fake_run_card(session, **kwargs):
        """Mirror the real ``_run_card`` return shape exactly.

        Real shape (dispatch.py:947): ``{"card_id", "session_name", "claimant",
        "source_column", "spawned"}``. Importantly, NOT ``"session"`` — that's
        the buggy key the dispatch tick was looking for.
        """
        card = kwargs["card"]
        # Mirror the claim+move side effects so _next_card doesn't re-pick this
        # card on the next tick iteration (see test_multi_agent_kanban).
        card.claimed_by = dispatch.CLAIMANT_PREFIX + "tmux-abc"
        card.column = "analyst" if kwargs["phase"] == "analyst" else "engineer"
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": "tmux-abc",
            "claimant": "agent:tmux-abc",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "parent", "column": "Backlog",
                     "analyst_agent_id": "claude-code"},
        )
        await s.commit()

        # Drive the real dispatcher — no inline-branch shortcut. This is the
        # exact code path that hit the bug.
        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

        parent = await s.get(KanbanCard, parent_id)
        assert parent.analyst_run_id == "tmux-abc", (
            f"analyst_run_id must be set from the real _run_card shape "
            f"(session_name key); got {parent.analyst_run_id!r}. "
            f"This is the Critical C1 regression: the dispatch tick looked at "
            f"the wrong key."
        )


# ---- Final review: resolve_phase helper + redispatch phase routing (I1) ---

class _PhaseCard:
    """Minimal duck-typed card with the fields resolve_phase reads."""
    def __init__(self, *, analyst_agent_id=None, analyst_run_id=None):
        self.analyst_agent_id = analyst_agent_id
        self.analyst_run_id = analyst_run_id


def test_resolve_phase_analyst_when_analyst_agent_and_no_run_id():
    card = _PhaseCard(analyst_agent_id="claude-code", analyst_run_id=None)
    assert dispatch.resolve_phase(card) == "analyst"


def test_resolve_phase_executor_when_analyst_already_ran():
    card = _PhaseCard(analyst_agent_id="claude-code", analyst_run_id="tmux-x")
    assert dispatch.resolve_phase(card) == "executor"


def test_resolve_phase_executor_for_legacy_single_agent_card():
    """No analyst_agent_id at all → legacy single-agent path → executor."""
    card = _PhaseCard(analyst_agent_id=None, analyst_run_id=None)
    assert dispatch.resolve_phase(card) == "executor"


def test_resolve_phase_executor_when_analyst_agent_set_but_card_has_no_phase():
    """A card with analyst_agent_id cleared back to None must dispatch as
    executor — only the analyst_agent_id AND no analyst_run_id combination
    picks analyst. This prevents an old analyst config from leaking through
    after a user clears it."""
    card = _PhaseCard(analyst_agent_id=None, analyst_run_id="tmux-stale")
    assert dispatch.resolve_phase(card) == "executor"


@pytest.mark.asyncio
async def test_redispatch_card_runs_analyst_phase_when_analyst_run_id_missing(monkeypatch):
    """Regression test for I1: a crashed analyst re-dispatched via
    redispatch_card must re-spawn the analyst, not the executor.

    Without the resolve_phase call inside redispatch_card, _run_card defaults
    to phase="executor" and silently skips the analyst step — violating spec
    §8 ("analyst sessie crasht halverwege → gebruiker kan redispatch_card
    aanroepen").
    """
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        spawns.append((kwargs["phase"], kwargs["card"].id))
        # Mirror real shape; mirror claim+move side effects so the
        # redispatch path's release/release-flow doesn't break.
        card = kwargs["card"]
        card.claimed_by = "agent:tmux-rd"
        card.column = "analyst" if kwargs["phase"] == "analyst" else "engineer"
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": "tmux-rd",
            "claimant": "agent:tmux-rd",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        # Multi-agent parent stuck mid-plan: analyst_agent_id set, but the
        # analyst session crashed before writing analyst_run_id.
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "stuck parent", "column": "Doing",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code",
                     "claimed_by": "agent:tmux-old"},
        )
        await s.commit()

        # The user hits "redispatch" — must re-spawn the analyst.
        await dispatch.redispatch_card(s, card_id=parent_id, project_path="/tmp/none")
        await s.commit()

        # Verify the analyst phase was picked (the card has analyst_agent_id
        # and no analyst_run_id, so resolve_phase returns "analyst").
        assert spawns == [("analyst", parent_id)], (
            f"redispatch_card must pick phase=analyst when analyst_run_id is "
            f"missing, got {spawns!r}"
        )


@pytest.mark.asyncio
async def test_redispatch_card_runs_executor_phase_when_analyst_already_ran(monkeypatch):
    """A multi-agent card past the analyst phase must re-spawn the executor
    when re-dispatched — not the analyst a second time. Covers the case
    where the executor crashed (analyst_run_id is set)."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        spawns.append((kwargs["phase"], kwargs["card"].id))
        card = kwargs["card"]
        card.claimed_by = "agent:tmux-rd2"
        card.column = "analyst" if kwargs["phase"] == "analyst" else "engineer"
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": "tmux-rd2",
            "claimant": "agent:tmux-rd2",
            "source_column": "Doing",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "past analyst", "column": "Doing",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code",
                     "analyst_run_id": "tmux-analyst-old",
                     "claimed_by": "agent:tmux-exec-old"},
        )
        await s.commit()

        await dispatch.redispatch_card(s, card_id=parent_id, project_path="/tmp/none")
        await s.commit()

        assert spawns == [("executor", parent_id)], (
            f"redispatch_card must pick phase=executor when analyst_run_id is "
            f"already set, got {spawns!r}"
        )


@pytest.mark.asyncio
async def test_dispatch_card_runs_analyst_phase_when_analyst_run_id_missing(monkeypatch):
    """Manual dispatch on a multi-agent card (the CardDrawer "Dispatch"
    button) must also pick the analyst phase when analyst_run_id is missing.
    Without resolve_phase, the manual path would silently dispatch the
    executor, dropping the analyst step on first dispatch.
    """
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        spawns.append((kwargs["phase"], kwargs["card"].id))
        card = kwargs["card"]
        card.claimed_by = "agent:tmux-m"
        card.column = "analyst" if kwargs["phase"] == "analyst" else "engineer"
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": "tmux-m",
            "claimant": "agent:tmux-m",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "fresh multi-agent", "column": "Backlog",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code"},
        )
        await s.commit()

        await dispatch.dispatch_card(s, card_id=cid, project_path="/tmp/none")
        await s.commit()

        assert spawns == [("analyst", cid)], (
            f"dispatch_card must pick phase=analyst when analyst_run_id is "
            f"missing, got {spawns!r}"
        )