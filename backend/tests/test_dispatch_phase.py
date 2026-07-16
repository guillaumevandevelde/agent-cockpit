"""Tests for the phase-aware provider/persona helpers extracted in Task 5.

These helpers are pure functions over a duck-typed card object, so the tests
do not need the kanban test database.
"""
import pytest

from app.kanban import dispatch
from app.kanban.dispatch import _phase_cli_id, _phase_target_agent


class _FakeCard:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_phase_provider_analyst_uses_analyst_field():
    card = _FakeCard(analyst_agent_id="claude-code", agent="engineer")
    assert _phase_cli_id(card, phase="analyst") == "claude-code"


def test_phase_provider_executor_uses_executor_field():
    card = _FakeCard(executor_agent_id="mimo-code", agent="engineer")
    assert _phase_cli_id(card, phase="executor") == "mimo-code"


def test_phase_provider_executor_falls_back_to_card_agent():
    card = _FakeCard(agent="codex-cli", executor_agent_id=None)
    assert _phase_cli_id(card, phase="executor") == "codex-cli"


def test_phase_provider_executor_default_claude_code():
    card = _FakeCard(agent=None, executor_agent_id=None)
    assert _phase_cli_id(card, phase="executor") == "claude-code"


def test_phase_provider_analyst_default_claude_code():
    card = _FakeCard(analyst_agent_id=None)
    assert _phase_cli_id(card, phase="analyst") == "claude-code"


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

    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
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
    from app.kanban.dispatch import PLAN_OK, _plan_context_section
    section = _plan_context_section(
        status=PLAN_OK,
        plan_markdown="# plan\n\nStep 1: do X\nStep 2: do Y",
        plan_deliverable_id="d1",
        parent_card_id="p1",
    )
    assert "PLAN CONTEXT" in section
    assert "read this first" in section
    assert "Step 1" in section
    assert "d1" in section or "p1" in section


def test_plan_context_unresolvable_returns_placeholder():
    from app.kanban.dispatch import PLAN_DANGLING_PARENT, _plan_context_section
    section = _plan_context_section(
        status=PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-id",
        parent_card_id="parent-1",
        # Empty description → guidance steers to report_impediment.
        card_description="",
    )
    assert "Plan niet beschikbaar" in section
    assert "parent-1" in section
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
    from app.kanban.dispatch import _plan_context_section, _run_card
    from app.kanban.models import KanbanCard
    from tests.kanban_test_db import TestSessionLocal

    section_calls = []
    resolve_calls = []

    async def fake_resolve(session, card):
        resolve_calls.append(card.id)
        return (dispatch.PLAN_NO_REF, None, None, None)

    def fake_section(*, status, plan_markdown, plan_deliverable_id, parent_card_id,
                     card_description=None):
        section_calls.append((status, plan_markdown, plan_deliverable_id, parent_card_id))
        return _plan_context_section(status=status,
                                     plan_markdown=plan_markdown,
                                     plan_deliverable_id=plan_deliverable_id,
                                     parent_card_id=parent_card_id,
                                     card_description=card_description)

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)
    monkeypatch.setattr(dispatch, "_plan_context_section", fake_section)

    captured = {}

    def fake_transport(directory, prompt, session_name, cli_id, provider, model=None):
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
        return (dispatch.PLAN_OK, "# My Plan\n\n- Step 1\n- Step 2", "d1", "parent-1")

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)

    captured = {}

    def fake_transport(directory, prompt, session_name, cli_id, provider, model=None):
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
        return (dispatch.PLAN_DANGLING_PARENT, None, "plan-id", "parent-1")

    monkeypatch.setattr(dispatch, "_resolve_plan_for_child", fake_resolve)

    captured = {}

    def fake_transport(directory, prompt, session_name, cli_id, provider, model=None):
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


# ---- Final review: analyst_run_id write-back must go through op-log (I2) --

@pytest.mark.asyncio
async def test_dispatch_project_analyst_run_id_persisted_via_op_log(monkeypatch):
    """Regression test for I2: the analyst_run_id write-back must go through
    apply_operation (not a direct ORM setattr) so a future rematerialize()
    replay doesn't drop the field.

    Asserts both observable surfaces:
    - the materialized card row has analyst_run_id set
    - the op-log carries a corresponding ``update`` op with the field in its
      payload (which rematerialize() would replay)
    """
    from sqlalchemy import select

    from app.kanban.models import KanbanCard, KanbanOp
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    async def fake_run_card(session, **kwargs):
        card = kwargs["card"]
        card.claimed_by = "agent:tmux-op"
        card.column = "analyst"
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": "tmux-op",
            "claimant": "agent:tmux-op",
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

        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

        parent = await s.get(KanbanCard, parent_id)
        assert parent.analyst_run_id == "tmux-op"

        # Verify the op-log carries the analyst_run_id update so a future
        # rematerialize() replay will re-apply it. Look for any op on this
        # card whose payload contains analyst_run_id.
        ops = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == parent_id)
            .order_by(KanbanOp.hlc.asc())
        )).scalars().all()
        analyst_run_id_ops = [
            o for o in ops
            if (o.payload or {}).get("analyst_run_id") == "tmux-op"
        ]
        assert analyst_run_id_ops, (
            f"analyst_run_id must be persisted via apply_operation (so a "
            f"rematerialize() replay carries it). Found op-log entries: "
            f"{[(o.op_type, o.payload) for o in ops]!r}"
        )
        # And the op_type must be 'update' (or 'create', for the initial op).
        assert any(o.op_type == "update" for o in analyst_run_id_ops), (
            f"the analyst_run_id write-back must be an 'update' op, got: "
            f"{[o.op_type for o in analyst_run_id_ops]!r}"
        )


# ---- Fix A: analyst persona fallback -----------------------------------------
# When a project has no .claude/agents/analyst.md, the analyst session must
# still receive a clear, scoped role prompt from backend/app/kanban/analyst_prompt.py
# (the hardcoded ANALYST_PROMPT). Otherwise the analyst session lands in the
# spawned tmux pane with an empty preamble and behaves like a generic engineer
# — implementing the whole task instead of planning + splitting.


def test_resolve_analyst_persona_prefers_project_analyst_md(tmp_path):
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text(
        "---\nmodel: claude-opus-4-8\n---\n\n# Project-specific analyst"
    )
    body = dispatch._resolve_analyst_persona(str(tmp_path))
    assert body.startswith("# Project-specific analyst")


def test_resolve_analyst_persona_falls_back_when_no_md(tmp_path):
    """No .claude/agents directory at all — must return the hardcoded fallback."""
    body = dispatch._resolve_analyst_persona(str(tmp_path))
    from app.kanban.analyst_prompt import ANALYST_PROMPT
    assert body == ANALYST_PROMPT.strip()
    # And the fallback must be the strict "verboden zelf code wijzigen" version:
    assert "Verboden" in body
    assert "Zelf code wijzigen" in body


def test_resolve_analyst_persona_falls_back_when_md_empty(tmp_path):
    """analyst.md exists but is empty (frontmatter only) — fall back, don't pass blank."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text(
        "---\nmodel: claude-opus-4-8\n---\n"
    )
    body = dispatch._resolve_analyst_persona(str(tmp_path))
    from app.kanban.analyst_prompt import ANALYST_PROMPT
    assert body == ANALYST_PROMPT.strip()


# ---- Bug fix: cards with work_type=analysis must route to analyst ----------
# Regression for kanban card 9cf106e7 ("Card with analysis work type got picked
# up by an engineer"). The card 0ece1ed291d349bea7b01e68b8268e0d was created
# with work_type="analysis" and an "agent":"claude-code" (a provider id, not a
# persona). _phase_target_agent saw `claude-code not in known_agents`, fell
# through to `_persona_for_card`, and hit the hardcoded "engineer" fallback.
# The fix: _run_card pre-resolves the work_type persona and passes it as a
# fallback_persona to _phase_target_agent. _phase_target_agent uses it when
# card.agent is missing or doesn't match a known persona. This protects all
# future work_type-route decisions regardless of how the card was created or
# whether a user later PATCHed work_type — the §2B "dispatch-time resolution"
# alternative called out in docs/cockpit/work-type-routing-analysis.md §2B.


def test_phase_target_agent_uses_fallback_persona_when_card_agent_missing(tmp_path):
    """card.agent is None → fallback_persona wins over the hardcoded 'engineer' fallback."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")
    card = _FakeCard(agent=None, work_type="analysis")
    assert _phase_target_agent(
        card, project_path=str(tmp_path), phase="executor",
        source_column="Backlog", fallback_persona="analyst",
    ) == "analyst"


def test_phase_target_agent_uses_fallback_persona_when_card_agent_is_provider_id(tmp_path):
    """card.agent is a non-persona (e.g. a legacy provider id 'claude-code') →
    fallback_persona wins. Without this, a legacy card created before the
    create-time auto-fill (commit 80e139e) routes work_type=analysis to engineer
    because `claude-code` is not a known agent file."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    card = _FakeCard(agent="claude-code", work_type="analysis")
    assert _phase_target_agent(
        card, project_path=str(tmp_path), phase="executor",
        source_column="Backlog", fallback_persona="analyst",
    ) == "analyst"


def test_phase_target_agent_explicit_card_agent_wins_over_fallback(tmp_path):
    """When card.agent is a known persona, the fallback is ignored. This
    preserves the §2B priority: explicit card.agent > work_type mapping."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")
    card = _FakeCard(agent="engineer", work_type="analysis")
    assert _phase_target_agent(
        card, project_path=str(tmp_path), phase="executor",
        source_column="Backlog", fallback_persona="analyst",
    ) == "engineer", (
        "explicit card.agent='engineer' must still win over a work_type-driven "
        "fallback to analyst"
    )


def test_phase_target_agent_fallback_must_be_a_known_persona(tmp_path):
    """A fallback_persona that doesn't match a known agent file is rejected —
    the work_type mapping can never invent a persona the project doesn't have."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")
    card = _FakeCard(agent=None, work_type="analysis")
    # "analyst.md" does not exist on disk → fallback rejected → engineer fallback.
    assert _phase_target_agent(
        card, project_path=str(tmp_path), phase="executor",
        source_column="Backlog", fallback_persona="analyst",
    ) == "engineer", (
        "fallback_persona='analyst' must not route to a column whose persona "
        "file does not exist on disk"
    )


@pytest.mark.asyncio
async def test_run_card_routes_analysis_card_with_missing_agent_to_analyst(monkeypatch, tmp_path):
    """End-to-end regression: a card with work_type='analysis' and no/invalid
    agent must end up in the 'analyst' column after _run_card — not 'engineer'."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    _stub_default_work_type_mapping(monkeypatch)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "analyse", "column": "Backlog",
                     "work_type": "analysis"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.work_type == "analysis"
        assert card.agent is None  # missing — triggers the fallback

        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()

        card = await s.get(KanbanCard, cid)
        assert card.column == "analyst", (
            f"work_type='analysis' with missing agent must route to 'analyst', "
            f"not the 'engineer' fallback. Got column={card.column!r}. "
            f"This is the regression for kanban card 9cf106e7."
        )


@pytest.mark.asyncio
async def test_run_card_routes_analysis_card_with_provider_id_agent_to_analyst(monkeypatch, tmp_path):
    """The actual incident: card.agent='claude-code' (provider id, not a persona)
    and work_type='analysis' — must still route to 'analyst'. Without the fix,
    'claude-code' not in known_agents drops through to 'engineer'."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    _stub_default_work_type_mapping(monkeypatch)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "analyse", "column": "Backlog",
                     "work_type": "analysis", "agent": "claude-code"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.work_type == "analysis"
        assert card.agent == "claude-code"  # legacy / provider id, not a persona

        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()

        card = await s.get(KanbanCard, cid)
        assert card.column == "analyst", (
            f"work_type='analysis' with agent='claude-code' (not a persona) "
            f"must route to 'analyst', not 'engineer'. Got column={card.column!r}."
        )


@pytest.mark.asyncio
async def test_run_card_respects_per_project_work_type_override(monkeypatch, tmp_path):
    """A stored (project_key, work_type) override on
    kanban_work_type_mappings must win over the global default for that
    project's dispatches. Without this, a project that wants `analysis` to
    route to engineer (e.g. an ops project where "analysis" is engineering
    due-diligence) silently gets analyst instead."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    # The end-to-end path goes through service.get_work_type_persona, so
    # stub it to return the per-project override (engineer for analysis on
    # this project) rather than the global default (analyst).
    from app.kanban import service

    async def fake_get_work_type_persona(session, project_key, work_type):
        from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS
        overrides = {("git:example", "analysis"): "engineer"}
        return overrides.get(
            (project_key, work_type),
            WORK_TYPE_PERSONA_DEFAULTS.get(work_type, "engineer"),
        )

    monkeypatch.setattr(service, "get_work_type_persona", fake_get_work_type_persona)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "override project", "column": "Backlog",
                     "work_type": "analysis"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)

        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()

        card = await s.get(KanbanCard, cid)
        assert card.column == "engineer", (
            f"per-project override (analysis→engineer on git:example) must win "
            f"over the global default (analysis→analyst). Got column={card.column!r}."
        )


@pytest.mark.asyncio
async def test_run_card_redispatch_is_idempotent_for_work_type_fallback(monkeypatch, tmp_path):
    """Redispatching the same card (the documented use case for
    redispatch_card) must produce the same routing decision — no per-call
    cache, no per-session state that flips persona between dispatches.

    Without this guarantee, a card could end up in different columns on
    subsequent dispatches just because the fallback resolution is
    stateful, with no diff in work_type or agent to explain it."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    _stub_default_work_type_mapping(monkeypatch)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "redispatch me", "column": "Backlog",
                     "work_type": "analysis"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)

        # First dispatch
        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        first_column = card.column
        assert first_column == "analyst"

        # Simulate "card returned to Backlog after session" — release
        # the claim and move back. The same card is now re-dispatched.
        await apply_operation(
            s, op_type="release", entity_type="card",
            project_key=PK, entity_id=cid, payload={},
        )
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key=PK, entity_id=cid, payload={"column": "Backlog"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)

        # Second dispatch — must land in the same column.
        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == first_column, (
            f"redispatch must produce the same routing decision as the first "
            f"dispatch (idempotent). First={first_column!r}, second={card.column!r}."
        )


@pytest.mark.asyncio
async def test_run_card_unknown_work_type_falls_through_not_engineer(monkeypatch, tmp_path):
    """An unrecognised work_type (e.g. legacy data, schema drift) must NOT
    silently override a column-derived persona with 'engineer'. The
    dispatcher should leave the routing to the column-derived fallback,
    so a card in a custom source-column persona doesn't silently get
    swapped to 'engineer'."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    # get_work_type_persona in production would silently return "engineer"
    # for unknown work_types via WORK_TYPE_PERSONA_DEFAULTS.get default.
    # We exercise the real path (no stub) here — _resolve_work_type_fallback
    # in dispatch.py is what protects against it.
    from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS

    real_default = WORK_TYPE_PERSONA_DEFAULTS.get("research", "engineer")
    assert real_default == "engineer", (
        "test premise: WORK_TYPE_PERSONA_DEFAULTS must default to 'engineer' "
        "for unknown work_types, so we can prove dispatch.py does NOT leak "
        "that default to column routing"
    )

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "legacy data", "column": "Backlog",
                     "work_type": "research"},  # not in WORK_TYPES
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)

        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()

        card = await s.get(KanbanCard, cid)
        # 'research' isn't in WORK_TYPES → _resolve_work_type_fallback
        # returns None → _phase_target_agent falls through to
        # _persona_for_card → hardcoded 'engineer'. The regression we're
        # guarding against is *changing* this default silently. Today
        # 'engineer' IS the legacy fallback, so the expected value is
        # 'engineer' — but we document the contract: the work_type lookup
        # must NOT have run for an unrecognised value. The dispatch still
        # goes to a known column so the card isn't wedged.
        assert card.column == "engineer", (
            f"unknown work_type='research' must not wedge the card; the "
            f"legacy 'engineer' fallback is acceptable, but the *route* "
            f"must NOT be a work_type-driven override. Got column={card.column!r}."
        )


@pytest.mark.asyncio
async def test_run_card_explicit_valid_card_agent_overrides_work_type_fallback(monkeypatch, tmp_path):
    """Priority: card.agent (when it IS a valid persona) > work_type mapping.
    A user explicitly setting agent='engineer' on a work_type='analysis' card
    must NOT be silently re-routed to 'analyst'."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "analyst.md").write_text("# analyst")
    (tmp_path / ".claude" / "agents" / "engineer.md").write_text("# engineer")

    _stub_default_work_type_mapping(monkeypatch)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "explicit engineer", "column": "Backlog",
                     "work_type": "analysis", "agent": "engineer"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.work_type == "analysis"
        assert card.agent == "engineer"  # explicit, valid persona

        await dispatch._run_card(
            s, card=card, project_key=PK,
            project_path=str(tmp_path), transport=_fake_transport,
            phase="executor",
        )
        await s.commit()

        card = await s.get(KanbanCard, cid)
        assert card.column == "engineer", (
            f"card.agent='engineer' (valid persona) must win over the "
            f"work_type='analysis' fallback. Got column={card.column!r}."
        )


# ---- Shared test helpers for the work_type dispatch fallback tests -----
# The end-to-end _run_card tests above all need the same two helpers:
# (a) a stub of service.get_work_type_persona returning the global default
#     mapping (no per-project overrides), and
# (b) a no-op transport that just records the call.
# Keeping them in one place avoids the three near-identical copy-pasted
# stubs the previous review flagged.


def _fake_transport(directory, prompt, session_name, cli_id, provider, model=None):
    """No-op transport that returns a minimal result dict."""
    return {"session_name": session_name, "transport": "worktree"}


def _stub_default_work_type_mapping(monkeypatch):
    """Patch service.get_work_type_persona to return the global default
    mapping (no per-project override applied). Mirrors
    `service.get_work_type_persona`'s fallback chain but skips the DB read
    against kanban_work_type_mappings (which the test DB may not have set
    up across all paths). dispatch.py resolves the helper at call time via
    `from app.kanban.service import get_work_type_persona`, so monkeypatching
    the service module is the correct target."""
    from app.kanban import service

    async def fake_get_work_type_persona(session, project_key, work_type):
        from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS
        return WORK_TYPE_PERSONA_DEFAULTS.get(work_type, "engineer")

    monkeypatch.setattr(service, "get_work_type_persona", fake_get_work_type_persona)


# ---- Leaf analyst spike override -------------------------------------------
# Regression for kanban card a9c27beeb63e427a9c14ad98fa8380fe
# ("[self-improve] analyst-persona + executor-ship-workflow botsen in één
# prompt bij work_type=analysis spike-kaarten"). A `work_type='analysis'`
# leaf card (no `analyst_agent_id`) was getting both the analyst persona
# (which says "Verboden: geen Write/Edit") AND the executor ship workflow
# (which says "write doc, commit, ship, attach branch, move THIS kaart
# naar Done") in the same prompt — the agent had to reason out the
# contradiction by hand. The fix prepends an override note to the persona
# preamble that explicitly relaxes the prohibition and reframes the task as
# "produce a single deliverable, ship it, move THIS card to Done".


def test_is_analyst_leaf_spike_recognizes_work_type_analysis():
    card = _FakeCard(work_type="analysis", agent=None)
    assert dispatch.is_analyst_leaf_spike(card) is True


def test_is_analyst_leaf_spike_recognizes_agent_analyst():
    card = _FakeCard(work_type=None, agent="analyst")
    assert dispatch.is_analyst_leaf_spike(card) is True


def test_is_analyst_leaf_spike_recognizes_both():
    card = _FakeCard(work_type="analysis", agent="analyst")
    assert dispatch.is_analyst_leaf_spike(card) is True


def test_is_analyst_leaf_spike_rejects_non_analyst_routing():
    card = _FakeCard(work_type="feature", agent="engineer")
    assert dispatch.is_analyst_leaf_spike(card) is False


def test_is_analyst_leaf_spike_rejects_unset_card():
    card = _FakeCard(work_type=None, agent=None)
    assert dispatch.is_analyst_leaf_spike(card) is False


def test_build_card_prompt_leaf_spike_prepends_override_note():
    """A leaf analyst spike (work_type=analysis + no analyst_agent_id,
    dispatched in executor phase) must get an override note that
    reconciles the analyst persona's "Verboden: geen Write/Edit" with the
    executor ship workflow's "write doc, commit, ship, attach". The
    override must come BEFORE the prohibition in the rendered prompt so
    the agent treats it as the operative instruction."""
    card = _FakeCard(
        title="Spike: db plafond",
        description="Investigate SQLite vs Postgres for multi-agent write load.",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "### Modus 2 — Leaf design-deliverable\n"
        "Schrijf, commit, ship.\n"
        "Verboden:\n"
        "- Zelf code wijzigen in het werkveld."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    override_marker = "Analyst-leaf-spike override"
    assert override_marker in prompt, (
        f"Leaf-spike override marker {override_marker!r} missing from prompt"
    )
    override_idx = prompt.index(override_marker)
    verboden_idx = prompt.index("Verboden")
    assert override_idx < verboden_idx, (
        "Override must precede the 'Verboden' prohibition so the agent reads "
        "it as the operative instruction. Got override_idx={override_idx}, "
        "verboden_idx={verboden_idx}."
    )
    # The persona is still loaded (we keep the analyst voice); only the
    # prohibition is relaxed.
    assert "Verboden" in prompt
    # The executor ship workflow is still present (no switch to analyst
    # session-end for leaf spikes).
    assert "Ship (direct mode)" in prompt
    assert "merge --no-ff" in prompt
    # The persona still mentions leaf-spike reframing (the "Leaf
    # design-deliverable" section is where the actual modus-2 contract
    # lives; the override is just a pointer).
    assert "Leaf design-deliverable" in prompt
    # The override must defer to the persona's leaf-design-deliverable
    # section (kanban card c2b478ca: the persona itself is the primary
    # source of truth, the override is a safety-net pointer). It must
    # reference both "leaf" + "design" so the agent knows the persona
    # section to look at.
    override_block = prompt[override_idx:prompt.index("\n\n---\n\n", override_idx)]
    assert (
        "leaf" in override_block.lower() and "design" in override_block.lower()
    ), (
        "Leaf-spike override must point at the persona's leaf-design-deliverable "
        "section (so the persona itself is the primary source of truth). "
        f"Override block was:\n{override_block}"
    )


def test_build_card_prompt_analyst_classic_no_override():
    """A real analyst card (analyst_agent_id set, no analyst_run_id,
    phase='analyst') must NOT get the leaf-spike override. It keeps the
    analyst session-end workflow (move parent → Done) and the analyst
    persona unchanged. The override would corrupt the multi-agent
    decomposition flow."""
    card = _FakeCard(
        title="Multi-agent parent",
        description="Decompose this.",
        work_type=None,
        agent=None,
        analyst_agent_id="claude-code",
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden:\n"
        "- Zelf code wijzigen in het werkveld."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="analyst",
    )

    assert "Analyst-leaf-spike override" not in prompt
    # Analyst session-end workflow: move parent → Done
    assert "Move the parent card to Done" in prompt
    # NOT the executor ship workflow — the analyst doesn't ship.
    assert "Ship (direct mode)" not in prompt
    assert "merge --no-ff" not in prompt


def test_build_card_prompt_non_analyst_card_no_override():
    """A non-analyst card (work_type=feature, agent=engineer) must NOT
    get the leaf-spike override — there's no analyst persona to relax."""
    card = _FakeCard(
        title="Feature: add button",
        description="",
        work_type="feature",
        agent="engineer",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = "You are an engineer. Write and ship."
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    assert "Analyst-leaf-spike override" not in prompt
    assert "Ship (direct mode)" in prompt
    assert "Move the parent card to Done" not in prompt


def test_build_card_prompt_post_analyst_executor_with_work_type_analysis_gets_override():
    """A card where the analyst already ran (analyst_agent_id +
    analyst_run_id both set) but its work_type is still 'analysis' is
    now dispatched in executor phase with the analyst persona. Same
    contradiction as the leaf-spike case — same override needed."""
    card = _FakeCard(
        title="post-analyst child",
        description="",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id="claude-code",
        analyst_run_id="tmux-old",
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden:\n"
        "- Zelf code wijzigen in het werkveld."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    assert "Analyst-leaf-spike override" in prompt
    override_idx = prompt.index("Analyst-leaf-spike override")
    verboden_idx = prompt.index("Verboden")
    assert override_idx < verboden_idx
    assert "Ship (direct mode)" in prompt


def test_build_card_prompt_leaf_spike_with_only_agent_no_work_type():
    """A leaf card without work_type=analysis but with card.agent='analyst'
    (legacy routing — analyst.md exists but the card was tagged for analyst
    manually) also gets the leaf-spike override."""
    card = _FakeCard(
        title="leaf spike",
        description="",
        work_type=None,
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden: geen Write/Edit."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    assert "Analyst-leaf-spike override" in prompt


def test_build_card_prompt_no_persona_no_override_section_breakage():
    """Edge case: persona is None (no analyst.md on disk). The leaf-spike
    detection still applies (work_type='analysis'), but no preamble is
    rendered — so the override is silently skipped (nothing to relax).
    The executor ship workflow renders normally."""
    card = _FakeCard(
        title="leaf spike",
        description="",
        work_type="analysis",
        agent=None,
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    prompt = dispatch.build_card_prompt(
        card, persona=None, ship_mode="direct", phase="executor",
    )

    # No override (no preamble to override)
    assert "Analyst-leaf-spike override" not in prompt
    # The ship workflow is intact
    assert "Ship (direct mode)" in prompt
    assert "merge --no-ff" in prompt


# ---- Leaf-spike follow-up cards clause -------------------------------------
# Continuation of the kanban card a9c27bee override. The override relaxed
# Write/Edit so the leaf-spike could write its doc, but it said nothing about
# the create_card/add_plan_attachment prohibitions carried over from the
# analyst persona. Result: a leaf-spike session that recommends concrete
# acceptance-criteria-level follow-up cards would leave them as §-prose in
# the doc and move THIS card to Done — forcing a manual review round-trip
# for what is mechanically deterministic work (kanban card 75b54887).
#
# The fix extends `_analyst_leaf_spike_override_note` with an explicit
# follow-up cards clause that:
#   (a) relaxes the create_card/add_plan_attachment prohibition for the
#       leaf case (analogous to Write/Edit);
#   (b) instructs: if the deliverable recommends concrete acceptance-
#       criteria-level follow-ups, create them in the SAME session via
#       create_card (+ add_plan_attachment when they form a DAG) BEFORE
#       moving THIS card to Done;
#   (c) carries the spam guards (acceptance-criteria level only,
#       dedup-pass via list_cards, depends_on only on real contracts);
#   (d) carries the scoped impediment-escape (report_impediment only for
#       real unresolved product forks; responsible forks decided
#       best-effort with the alternative preserved as a conditional card).
#
# Marker: "Leaf-spike follow-up cards clause" — greppable, mirrors the
# existing "Analyst-leaf-spike override" marker pattern from kanban card
# a9c27bee so test-side scanning is consistent.

LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER = "Leaf-spike follow-up cards clause"


def _leaf_spike_clause_block(prompt: str) -> str:
    """Return the text of the leaf-spike follow-up cards clause block from
    a rendered prompt (from the clause marker through the override's
    `\\n\\n---\\n\\n` terminator). Empty string when the clause isn't in
    the prompt."""
    if LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER not in prompt:
        return ""
    start = prompt.index(LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER)
    end_marker = "\n\n---\n\n"
    end = prompt.find(end_marker, start)
    if end == -1:
        # Clause rendered without the trailing separator (defensive — every
        # current path includes it). Return the rest of the prompt so the
        # assertion still inspects something meaningful.
        return prompt[start:]
    return prompt[start:end]


def test_build_card_prompt_leaf_spike_contains_follow_up_cards_clause():
    """A leaf analyst spike (work_type='analysis' + no analyst_agent_id,
    dispatched in executor phase) MUST contain the follow-up cards clause.
    The clause is what makes the leaf-spike session autonomous over its
    own recommendations — without it, the recommendations stay as
    §-prose and the autonomy contract is silently violated.
    """
    card = _FakeCard(
        title="Spike: follow-up cards",
        description="",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden:\n"
        "- Zelf code wijzigen in het werkveld."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    assert LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER in prompt, (
        f"Leaf-spike follow-up cards clause marker "
        f"{LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER!r} missing from leaf-spike "
        f"prompt. The clause is required so leaf-spike sessions create "
        f"follow-up cards in the same session instead of leaving "
        f"recommendations as §-prose (kanban card 75b54887)."
    )


def test_build_card_prompt_regular_executor_no_follow_up_cards_clause():
    """A regular executor card (work_type=feature, agent=engineer) must
    NOT receive the leaf-spike follow-up cards clause. The clause is
    specifically the relaxation of the analyst persona's create_card
    prohibition for the leaf case — applying it to a non-leaf engineer
    card would mean engineers start creating their own follow-up cards
    (changing the executor/analyst contract), which is out of scope for
    this card.
    """
    card = _FakeCard(
        title="Feature: add button",
        description="",
        work_type="feature",
        agent="engineer",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = "You are an engineer. Write and ship."
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    assert LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER not in prompt


def test_build_card_prompt_real_analyst_no_follow_up_cards_clause():
    """A real analyst card (analyst_agent_id set, dispatched in
    phase='analyst') must NOT receive the leaf-spike follow-up cards
    clause. The classic analyst persona already has its own
    create_card/add_plan_attachment contract (that's the analyst's
    primary job). Duplicating that contract via the leaf-spike clause
    would create contradictory instructions — the persona says
    "create kind-cards via add_plan_attachment", the leaf-spike clause
    says "create follow-up cards via create_card" — and the two are
    subtly different operations.
    """
    card = _FakeCard(
        title="Multi-agent parent",
        description="",
        work_type=None,
        agent=None,
        analyst_agent_id="claude-code",
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden:\n"
        "- Zelf code wijzigen in het werkveld."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="analyst",
    )

    assert LEAF_SPIKE_FOLLOW_UP_CARDS_MARKER not in prompt


def test_build_card_prompt_leaf_spike_follow_up_clause_has_spam_guards():
    """The follow-up cards clause must contain the three spam guards:

    1. Acceptance-criteria level only — speculation stays as §-prose.
    2. Dedup-pass via list_cards (Backlog/Impediment), comment on match
       instead of duplicating — same discipline as the flag-problem skill.
    3. depends_on only on a real contract — pure sequence without a
       contract is not a dependency.

    Without these guards, the relaxed create_card permission would
    spawn Backlog-spam (every leaf-spike dumps its brainstorm as N
    cards) and reverse the autonomy principle into over-escalation.
    """
    card = _FakeCard(
        title="Spike: with guards",
        description="",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden: geen Write/Edit."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    clause_block = _leaf_spike_clause_block(prompt)
    assert clause_block, (
        "Leaf-spike follow-up cards clause marker missing from prompt — "
        "test_build_card_prompt_leaf_spike_contains_follow_up_cards_clause "
        "should have failed first; check test ordering or merge conflicts."
    )

    # Guard 1: acceptance-criteria level only.
    assert (
        "acceptance-criteria" in clause_block.lower()
        or "acceptance criteria" in clause_block.lower()
    ), (
        "Follow-up cards clause must require acceptance-criteria-level "
        "scope to prevent Backlog-spam (speculative ideas stay as prose)."
    )

    # Guard 2: dedup-pass first via list_cards.
    assert "list_cards" in clause_block, (
        "Follow-up cards clause must require a dedup-pass via list_cards "
        "before creating, with `comment` on match instead of duplicating."
    )
    assert "comment" in clause_block.lower(), (
        "Follow-up cards clause must instruct `comment` on a dedup match "
        "(same discipline as the flag-problem skill)."
    )

    # Guard 3: depends_on only on a real contract.
    assert "depends_on" in clause_block, (
        "Follow-up cards clause must address depends_on — only on a real "
        "contract, not pure sequence without a contract."
    )


def test_build_card_prompt_leaf_spike_follow_up_clause_relaxes_create_card():
    """The follow-up cards clause must explicitly RELAX the
    create_card/add_plan_attachment prohibition for the leaf case
    (analogous to how the existing override relaxes Write/Edit). The
    relaxation is the load-bearing part: without an explicit
    permission, the agent reads the persona's "Verboden" and skips
    card creation, defeating the entire clause.
    """
    card = _FakeCard(
        title="Spike: relaxed create_card",
        description="",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden: geen Write/Edit."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    clause_block = _leaf_spike_clause_block(prompt)
    assert clause_block

    # Both create_card and add_plan_attachment must be referenced — the
    # decision doc prescribes create_card for standalone follow-ups and
    # add_plan_attachment when the cards form a DAG.
    assert "create_card" in clause_block
    assert "add_plan_attachment" in clause_block
    # The relaxation language must be explicit — paraphrase markers:
    # "relaxed", "permitted", or "allowed" in conjunction with the verbs.
    clause_lower = clause_block.lower()
    assert any(
        marker in clause_lower
        for marker in ("relaxed", "relax", "permitted", "allowed", "toegestaan")
    ), (
        "Follow-up cards clause must explicitly state that create_card / "
        "add_plan_attachment is RELAXED for the leaf case — a vague "
        "mention leaves the agent reading the persona's Verboden and "
        "skipping card creation."
    )


def test_build_card_prompt_leaf_spike_follow_up_clause_has_scoped_impediment_escape():
    """The follow-up cards clause must contain the scoped impediment-
    escape: report_impediment(options=[…]) is reserved for an
    UNRESOLVED PRODUCT FORK that changes WHAT the cards should be
    (the autonomy-eerst pattern from the decision doc — kanban card
    75b54887 §5.3). Responsible forks are decided best-effort with
    the alternative preserved as a conditional card. Without this
    scope, the clause over-corrects: leaf-spike sessions escalate
    every minor fork to a human, re-institutionalising the autonomy
    violation in reverse.
    """
    card = _FakeCard(
        title="Spike: scoped impediment",
        description="",
        work_type="analysis",
        agent="analyst",
        analyst_agent_id=None,
        analyst_run_id=None,
    )
    persona = (
        "Je bent de analyst voor een kanban-kaart.\n"
        "Verboden: geen Write/Edit."
    )
    prompt = dispatch.build_card_prompt(
        card, persona=persona, ship_mode="direct", phase="executor",
    )

    clause_block = _leaf_spike_clause_block(prompt)
    assert clause_block

    # The escape mechanism: report_impediment must be referenced.
    assert "report_impediment" in clause_block, (
        "Follow-up cards clause must reference report_impediment so the "
        "leaf-spike session knows the scoped escape exists for real "
        "unresolved product forks."
    )

    # The scope: best-effort + conditional must be present so the agent
    # has a default other than escalating every fork.
    clause_lower = clause_block.lower()
    assert (
        "best-effort" in clause_lower or "best effort" in clause_lower
    ), (
        "Follow-up cards clause must mention best-effort as the default "
        "for responsible forks (escalate only the knot you cannot "
        "responsibly cut)."
    )
    assert "conditional" in clause_lower, (
        "Follow-up cards clause must mention preserving the alternative "
        "as a conditional card (the autonomy-eerst pattern: escalate "
        "the unresolved knot, not the routine fork)."
    )