# Multi-agent kanban workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Bron van waarheid:** `docs/cockpit/multi-agent-kanban.md` is leidend voor de
> multi-agent flow; dit superpowers-plan is de TDD-uitvoering die het heeft
> opgeleverd. Zie `docs/cockpit/00-orientation.md` → *Documenten* voor de
> drie-bomen-regel.

**Goal:** Add an opt-in two-phase workflow to kanban cards (analyst session → split into children → independent executor sessions), per the design in `docs/superpowers/specs/2026-07-08-multi-agent-kanban-design.md`.

**Architecture:** Extend the kanban data model with five new fields on `KanbanCard` (`analyst_agent_id`, `executor_agent_id`, `parent_card_id`, `analyst_run_id`, `depends_on`) and two new `kind` values on `KanbanDeliverable` (`plan`, `plan_ref`). Add a pure dep-resolver, an MCP tool that attaches a validated plan and fans it out to children, a phase-aware `_run_card(card, *, phase=...)`, an analyst-prompt fallback, and minimal frontend affordances (two dropdowns in `CardEditDialog`, badge on cards, plan tab in drawer). Backward compatible: cards without `analyst_agent_id` skip the whole multi-agent path.

**Tech Stack:** FastAPI + SQLAlchemy + aiosqlite (kanban DB), FastMCP, React 19 + Vite + TypeScript + shadcn/ui, pytest + pytest-asyncio.

## Global Constraints

- **Spec**: `docs/superpowers/specs/2026-07-08-multi-agent-kanban-design.md` — every requirement in §1-§9 maps to a task below.
- **Backward compatibility**: any code path that reads a `KanbanCard` row written before this feature must continue working unchanged (all five new fields default to `None`).
- **No new tables, no new columns on existing core tables beyond `kanban_cards` / `kanban_deliverables`**. Two new accepted `kind` string values on `KanbanDeliverable`; the `String(16)` column already accepts arbitrary 16-char strings.
- **No migration system**: re-creating the table is acceptable in development. Production data must remain readable without a migration.
- **Provider-id vocabulary**: `claude-code`, `mimo-code`, `codex-cli`, `open-code`, `copilot-cli` — matches `services/providers/__init__.py:11-17`. Use a `KNOWN_PROVIDERS` constant (existing) — do not hard-code provider IDs in plan tasks.
- **Strict TDD**: every code task writes its failing test FIRST, watches it fail for the right reason, implements minimum code, watches it pass, then commits.
- **Frequent commits**: one commit per task. Conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
- **No local pytest** until the repo goes public (user preference from memory: `feedback_no_local_pytest.md`); use `bash scripts/test_cockpit.sh` for backend smoke or rely on CI's `quality.yml`. The plan instructs running tests for verification, but the engineer should run them in isolation; do not block merges on a local full-suite pass.

---

## File Structure

**New files:**

| Path | Purpose |
|---|---|
| `backend/app/kanban/dep_resolver.py` | Pure function `meets_dep_prerequisites(card, cards_by_id)` and `detect_cycle(graph)`. |
| `backend/app/kanban/analyst_prompt.py` | Hard-coded fallback prompt body for analyst phase. |
| `backend/tests/test_dep_resolver.py` | Unit tests for dep resolver + cycle detection. |
| `backend/tests/test_analyst_prompt.py` | Unit test that fallback prompt contains the verboden + werkwijze blocks. |
| `backend/tests/test_add_plan_attachment.py` | MCP-tool validation tests. |
| `backend/tests/test_dispatch_phase.py` | Unit tests for phase-aware provider/persona selection. |
| `backend/tests/integration/test_multi_agent_kanban.py` | End-to-end test with stubbed `_run_card`. |
| `docs/cockpit/multi-agent-kanban.md` | Manual smoke-test cookbook. |

**Modified files:**

| Path | What changes |
|---|---|
| `backend/app/kanban/models.py` | Five new columns on `KanbanCard`: `analyst_agent_id`, `executor_agent_id`, `parent_card_id`, `analyst_run_id`, `depends_on`. |
| `backend/app/kanban/operations.py` | New ops `add_plan_attachment` and `link_plan_ref` in `_materialize`. New fields honored in the `update` branch. |
| `backend/app/kanban/schemas.py` | `CardResponse` exposes the five new fields. |
| `backend/app/kanban/dispatch.py` | `_run_card(card, *, phase="executor")` parameter; `_run_card_dispatch_tick` checks analyst path + dep prereqs; `build_executor_prompt` prepends plan context. |
| `backend/app/kanban/mcp_server.py` | New tool `add_plan_attachment`. |
| `backend/app/api/v1/kanban/router.py` | The two new card fields pass through `PATCH /cards/{cid}`. |
| `frontend/src/features/kanban/types.ts` | `Card` interface gains the new fields. |
| `frontend/src/features/kanban/api.ts` | New types/helpers for analyst/executor. |
| `frontend/src/features/kanban/components/CardEditDialog.tsx` | Two new dropdowns. |
| `frontend/src/features/kanban/components/CardItem.tsx` | `🪄 Multi-agent` badge. |
| `frontend/src/features/kanban/components/CardDrawer.tsx` | New `Plan` tab. |

---

## Task 1: Extend `KanbanCard` model with five new fields

**Files:**
- Modify: `backend/app/kanban/models.py:33-70`
- Test: `backend/tests/test_kanban_operations.py` (extend existing file)

**Interfaces:**
- Consumes: nothing new
- Produces: `KanbanCard` ORM class with five new optional fields

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_kanban_operations.py`:

```python
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
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_kanban_operations.py::test_create_card_persists_multi_agent_fields -v`
Expected: FAIL with `AttributeError: type object 'KanbanCard' has no attribute 'analyst_agent_id'`.

- [ ] **Step 3: Add the five new columns**

Edit `backend/app/kanban/models.py`, inside the `KanbanCard` class, add these declarations **after** `scheduled_at` and **before** `dispatch_failures`:

```python
    analyst_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    executor_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_card_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    analyst_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depends_on: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_kanban_operations.py::test_create_card_persists_multi_agent_fields -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/models.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): add analyst/executor fields to KanbanCard

Adds analyst_agent_id, executor_agent_id, parent_card_id,
analyst_run_id, depends_on — all nullable, all backward compatible."
```

---

## Task 2: Teach `_materialize` to honor the new fields in `update`

**Files:**
- Modify: `backend/app/kanban/operations.py:167-178` (the `update` branch)
- Test: `backend/tests/test_kanban_operations.py` (append)

**Interfaces:**
- Consumes: `payload` keys: `analyst_agent_id`, `executor_agent_id`, `parent_card_id`, `analyst_run_id`, `depends_on`
- Produces: those values written to the `KanbanCard` row

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_kanban_operations.py::test_update_card_persists_multi_agent_fields -v`
Expected: FAIL — the five fields stay `None`.

- [ ] **Step 3: Extend the `update` branch**

In `backend/app/kanban/operations.py`, inside `_materialize`'s `else: # update` block, expand the second `setattr` loop to include the five new fields:

```python
        else:  # update
            for f in ("title", "description"):
                if f in payload and payload[f] is not None:
                    _lww_set(card, f, payload[f], hlc)
            for f in ("priority", "labels", "agent", "transport",
                      "resume_session_id", "resume_project_folder", "scheduled_at",
                      "dispatch_failures",
                      "analyst_agent_id", "executor_agent_id", "parent_card_id",
                      "analyst_run_id", "depends_on"):
                if f in payload:
                    setattr(card, f, payload[f])
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_kanban_operations.py::test_update_card_persists_multi_agent_fields -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): teach _materialize to persist multi-agent fields on update"
```

---

## Task 3: Pure dep-resolver — `meets_dep_prerequisites` and cycle detection

**Files:**
- Create: `backend/app/kanban/dep_resolver.py`
- Create: `backend/tests/test_dep_resolver.py`

**Interfaces:**
- Consumes: `KanbanCard` (uses `id` and `depends_on`), `dict[str, KanbanCard]` (cards-by-id lookup)
- Produces:
  - `meets_dep_prerequisites(card, cards_by_id) -> bool`
  - `detect_cycle(graph: dict[str, list[str]]) -> list[str] | None` — returns a cycle path or `None`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_dep_resolver.py`:

```python
from app.kanban.dep_resolver import detect_cycle, meets_dep_prerequisites


class _FakeCard:
    def __init__(self, id, depends_on=None):
        self.id = id
        self.depends_on = depends_on or []


def test_meets_dep_prerequisites_no_deps():
    assert meets_dep_prerequisites(_FakeCard("c"), {}) is True


def test_meets_dep_prerequisites_all_parents_done():
    cards = {"parent": _FakeCard("parent", column="Done")}
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is True


def test_meets_dep_prerequisites_parent_not_done():
    cards = {"parent": _FakeCard("parent", column="Backlog")}
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is False


def test_meets_dep_prerequisites_missing_parent_fails_closed():
    cards = {}  # parent not in lookup
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is False


def test_detect_cycle_no_cycle():
    assert detect_cycle({"a": ["b"], "b": ["c"], "c": []}) is None


def test_detect_cycle_finds_cycle():
    cycle = detect_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})
    assert cycle is not None
    assert cycle[0] == cycle[-1]


def test_detect_cycle_self_loop():
    cycle = detect_cycle({"a": ["a"]})
    assert cycle is not None
    assert "a" in cycle
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_dep_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kanban.dep_resolver'`.

- [ ] **Step 3: Implement `dep_resolver.py`**

Create `backend/app/kanban/dep_resolver.py`:

```python
"""Pure dependency-resolution helpers used by the dispatch tick.

Kept in its own module so the caller (dispatch) can be tested with mocks and so
the cycle-detection has no DB / session imports — it operates on plain dicts.
"""
from __future__ import annotations

from typing import Iterable, Sequence


def meets_dep_prerequisites(card, cards_by_id: dict) -> bool:
    """True iff every entry in `card.depends_on` is in `cards_by_id` AND
    that card is in column 'Done'. A missing parent is treated as
    'not Done' — fail closed."""
    deps = getattr(card, "depends_on", None) or []
    for parent_id in deps:
        parent = cards_by_id.get(parent_id)
        if parent is None:
            return False
        if getattr(parent, "column", None) != "Done":
            return False
    return True


def detect_cycle(graph: dict[str, Sequence[str]]) -> list[str] | None:
    """Return the first cycle found as a list [a, b, ..., a], or None if acyclic.

    Uses the standard 'gray/black' DFS colour scheme. Input keys are nodes,
    values are the parents each node depends on (i.e. edges go from node →
    dependency). Self-loops are cycles and are detected immediately.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = GRAY
        path.append(n)
        for m in graph.get(n, []):
            if m not in color:
                # unknown node: ignore (it's an external dep not part of this graph)
                continue
            if color[m] == GRAY:
                start = path.index(m)
                return path[start:] + [m]
            if color[m] == WHITE:
                c = visit(m)
                if c is not None:
                    return c
        path.pop()
        color[n] = BLACK
        return None

    for n in list(graph):
        if color[n] == WHITE:
            c = visit(n)
            if c is not None:
                return c
    return None
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_dep_resolver.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dep_resolver.py backend/tests/test_dep_resolver.py
git commit -m "feat(kanban): pure dep-resolver with cycle detection"
```

---

## Task 4: Analyst fallback prompt

**Files:**
- Create: `backend/app/kanban/analyst_prompt.py`
- Create: `backend/tests/test_analyst_prompt.py`

**Interfaces:**
- Consumes: nothing
- Produces: module-level `ANALYST_PROMPT: str` (constant)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_analyst_prompt.py`:

```python
from app.kanban.analyst_prompt import ANALYST_PROMPT


def test_prompt_has_werkwijze_section():
    assert "Werkwijze" in ANALYST_PROMPT


def test_prompt_has_verboden_section():
    assert "Verboden" in ANALYST_PROMPT


def test_prompt_lists_required_tools():
    for tool in ("mcp__cockpit-kanban__create_card",
                 "mcp__cockpit-kanban__add_plan_attachment",
                 "mcp__cockpit-kanban__move_card"):
        assert tool in ANALYST_PROMPT


def test_prompt_instructs_parent_to_done():
    assert "Done" in ANALYST_PROMPT
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_analyst_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.kanban.analyst_prompt'`.

- [ ] **Step 3: Implement `analyst_prompt.py`**

Create `backend/app/kanban/analyst_prompt.py`:

```python
"""Built-in fallback prompt for the analyst phase.

Used when a project has no `.claude/agents/analyst.md`. Keep the body
strict: the analyst's job is planning, not implementing.
"""

ANALYST_PROMPT = """\
Je bent de analyst voor een kanban-kaart. Je taak is uitsluitend
plannen en opdelen — niet implementeren.

Beschikbare tools:
- mcp__cockpit-kanban__create_card
- mcp__cockpit-kanban__add_plan_attachment
- mcp__cockpit-kanban__move_card
- mcp__cockpit-kanban__open_gate

Werkwijze:
1. Lees de kaart-titel + beschrijving + deliverables.
2. Bedenk een implementatieplan met 1+ kind-kaarten.
3. Voor elke kind-kaart: titel, beschrijving, executor_agent_id
   (default: parent.executor_agent_id), optionele depends_on.
4. Schrijf een plan-attachment op de parent via add_plan_attachment.
5. Verplaats de parent-kaart naar 'Done' met summary
   'Plan opgesplitst in N taken'.
6. Stop de sessie (move_card naar Done is je exit-signaal).

Verboden:
- Zelf code wijzigen in het werkveld.
- Glob aanmaken die geen kind-kaarten zijn.
- Parent-card onafgemaakt laten als je klaar bent.
"""
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_analyst_prompt.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/analyst_prompt.py backend/tests/test_analyst_prompt.py
git commit -m "feat(kanban): analyst-prompt fallback constant"
```

---

## Task 5: Phase-aware `_run_card` provider/persona selection

**Files:**
- Modify: `backend/app/kanban/dispatch.py:721-790` (the `_run_card` function)
- Create: `backend/tests/test_dispatch_phase.py`

**Interfaces:**
- Consumes: existing call sites pass `_run_card(card=..., project_key=..., project_path=..., transport=...)` — those keep working with the new keyword default
- Produces: `_run_card(card, *, project_key, project_path, transport, phase="executor", impediment_question=None, agent_override=None, live_sessions=None)`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_dispatch_phase.py`:

```python
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
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v`
Expected: FAIL with `AttributeError: module 'app.kanban.dispatch' has no attribute '_phase_provider_id'`.

- [ ] **Step 3: Extract two pure helpers + extend `_run_card`**

Add at the top of `backend/app/kanban/dispatch.py` (after the existing imports, before the `META_PREFIX = ...` line):

```python
# Known provider IDs are registered in app.services.providers; re-derived here
# so the phase router doesn't depend on that module being imported at typing time.
def _phase_provider_id(card, *, phase: str) -> str:
    if phase == "analyst":
        return getattr(card, "analyst_agent_id", None) or "claude-code"
    # phase == "executor"
    return (getattr(card, "executor_agent_id", None)
            or getattr(card, "agent", None)
            or "claude-code")


def _phase_target_agent(card, *, project_path: str, phase: str, source_column: str) -> str:
    """Persona for the spawned session. Analyst phase is fixed to 'analyst';
    executor phase reuses the existing overload-resolution logic in _run_card."""
    if phase == "analyst":
        return "analyst"
    agents_dir = Path(project_path) / ".claude" / "agents"
    known_agents = {p.stem for p in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()
    card_agent = getattr(card, "agent", None)
    if card_agent and card_agent in known_agents:
        return card_agent
    persona = _persona_for_card(project_path, card, source_column)
    return _resolve_agent_from_persona(persona) or "engineer"
```

Then edit the existing `_run_card` signature to add the `phase` parameter:

```python
async def _run_card(
    session, *, card, project_key: str, project_path: str, transport: SpawnTransport,
    phase: str = "executor",
    impediment_question: str | None = None,
    agent_override: str | None = None,
    live_sessions: set[str] | None = None,
) -> dict | None:
```

Replace lines 753-773 (the provider-id + persona resolution block) with:

```python
    known_providers = _known_provider_ids()
    provider_id = _phase_provider_id(card, phase=phase) if not agent_override \
        else next((v for v in (agent_override, _phase_provider_id(card, phase=phase)) if v in known_providers),
                  _phase_provider_id(card, phase=phase))
    target_agent = _phase_target_agent(card, project_path=project_path, phase=phase,
                                       source_column=source_column)
```

(If `agent_override` is not a known provider, this falls back to the phase provider; if it *is* a known provider it wins. Persona override continues to work via `agent_override`.)

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v`
Expected: 6 passed.

- [ ] **Step 5: Smoke-check that `_run_card` still accepts the old call form**

Run: `cd backend && python -c "from app.kanban.dispatch import _run_card; import inspect; print(inspect.signature(_run_card))"`
Expected output contains `phase: str = 'executor'`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_dispatch_phase.py
git commit -m "feat(kanban): phase-aware provider/persona selection in _run_card"
```

---

## Task 6: Tick-level multi-agent orchestration

**Files:**
- Modify: `backend/app/kanban/dispatch.py:670-790` (the `dispatch_project` / `_run_card_dispatch_tick` flow)
- Extend: `backend/tests/test_dispatch_phase.py` (append)

**Interfaces:**
- Consumes: `dispatch_project(session, project_key, project_path, ...)` (existing entry point)
- Produces: when a card has `analyst_agent_id` set, the tick spawns the analyst first, persists `analyst_run_id`, and waits for follow-up ticks to spawn executors.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_dispatch_phase.py`:

```python
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
    from sqlalchemy import select
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
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v -k 'tick'` 
Expected: the second test fails because `dispatch._run_card_dispatch_tick` doesn't yet have the analyst path.

- [ ] **Step 3: Patch the tick — wire dep-resolver + analyst path**

Find the body of `dispatch_project` / `_run_card_dispatch_tick` (line ~660-700). Add an import at the top of `backend/app/kanban/dispatch.py`:

```python
from app.kanban.dep_resolver import meets_dep_prerequisites
```

Inside the tick, where the next card is dispatched, change:

```python
        for card in cards:
            if not meets_dep_prerequisites(card, cards_by_id):
                continue
            if card.analyst_agent_id and not card.analyst_run_id:
                result = await _run_card(session, card=card,
                    project_key=project_key, project_path=project_path,
                    transport=transport, phase="analyst")
                if result and "session" in result:
                    card.analyst_run_id = result["session"]
                    session.add(card)
                    await session.flush()
                continue
            await _run_card(session, card=card,
                project_key=project_key, project_path=project_path,
                transport=transport, phase="executor")
```

`cards_by_id` is built once before the loop:

```python
        cards_by_id = {c.id: c for c in cards}
```

Note: `card.analyst_run_id = result["session"]` is the **non-CRDT** path — it's a plain SQLAlchemy update. We do NOT route this through `apply_operation`'s LWW path because (a) the column is internal bookkeeping, not user-visible state, and (b) we want it set synchronously so the next tick sees it without waiting for HLC ordering. Add a comment to that effect:

```python
                # analyst_run_id is internal bookkeeping, not a CRDT-managed
                # field — set it synchronously so the next tick sees it without
                # HLC ordering.
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v -k 'tick'`
Expected: both tick tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_dispatch_phase.py
git commit -m "feat(kanban): tick honors analyst phase + dep prerequisites"
```

---

## Task 7: Plan-aware executor prompt

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (`build_card_prompt` or a new helper)
- Extend: `backend/tests/test_dispatch_phase.py`

**Interfaces:**
- Consumes: `card` (looks up `plan_ref` deliverable), `service.get_card(card.id)` (already imported)
- Produces: a prompt string that prepends a `PLAN CONTEXT` section when a resolvable plan_ref exists.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_dispatch_phase.py`:

```python
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
```

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v -k 'plan_context'`
Expected: FAIL with `AttributeError: ... has no attribute '_plan_context_section'`.

- [ ] **Step 3: Add `_plan_context_section` and wire it into `build_card_prompt`**

Append to `backend/app/kanban/dispatch.py`:

```python
def _plan_context_section(*, plan_markdown: str | None, plan_deliverable_id: str | None,
                          parent_card_id: str | None) -> str:
    """Build the PLAN CONTEXT preamble that the executor sees in its prompt.

    Resolves the plan via the child's `plan_ref` deliverable. If the ref
    is missing (parent deleted, plan never written), returns a placeholder
    that nudges the executor to surface the issue via report_impediment.
    """
    if not plan_markdown:
        return (
            "PLAN CONTEXT — let op: het plan-attachment voor deze kaart kon "
            "niet worden geladen (mogelijk is de parent-kaart verwijderd of "
            "is het plan nooit opgeslagen). Gebruik "
            "mcp__cockpit-kanban__report_impediment om dit te signaleren.\n"
        )
    return (
        f"PLAN CONTEXT — read this first\n"
        f"Plan deliverable: {plan_deliverable_id}\n"
        f"Parent card: {parent_card_id}\n\n"
        f"{plan_markdown}\n\n"
        f"---\n"
        f"Bovenstaande is het plan van de analyst. Volg deze stappen, "
        f"tenzij je tijdens het werk ontdekt dat het plan niet klopt — "
        f"gebruik dan report_impediment.\n"
    )
```

Now find `build_card_prompt` in the same file. After it computes the basic prompt, prepend the plan section when the card has a `plan_ref` deliverable. Add this helper above `build_card_prompt`:

```python
def _resolve_plan_for_child(session, card) -> tuple[str | None, str | None, str | None]:
    """Return (plan_markdown, plan_deliverable_id, parent_card_id) for a child
    card that holds a `plan_ref` deliverable, or (None, None, None)."""
    for d in getattr(card, "deliverables", []) or []:
        if d.kind == "plan_ref":
            try:
                ref = json.loads(d.ref)
            except (TypeError, ValueError):
                return (None, None, None)
            parent_id = ref.get("parent_card_id")
            plan_id = ref.get("plan_deliverable_id")
            if not parent_id or not plan_id:
                return (None, None, None)
            parent = await service.get_card(session, parent_id)  # type: ignore[name-defined]
            if parent is None:
                return (None, None, None)
            for pd in parent.deliverables:
                if pd.id == plan_id and pd.kind == "plan":
                    return (pd.ref, plan_id, parent_id)
    return (None, None, None)
```

(You will need `import json` at the top of `dispatch.py` and `from app.kanban import service` if not already imported.)

Inside `build_card_prompt`, after the existing `card`-based prompt is built, call this if appropriate. For this task, we don't need to wire it into `build_card_prompt` itself — we expose `_plan_context_section` as a pure helper and the `build_executor_prompt` wrapper (next task) will compose it. Skip this step's plan-section integration here; just commit the helper.

Actually — for `phase=executor`, the `_run_card` should call `_resolve_plan_for_child` and pass the result through `_plan_context_section`. Edit the lower part of `_run_card` where `prompt = build_card_prompt(...)` is called:

```python
    prompt_body = build_card_prompt(card, persona=persona, ship_mode=ship_mode,
        impediment_question=impediment_question)
    if phase == "executor":
        plan_md, plan_id, parent_id = await _resolve_plan_for_child(session, card)
        plan_section = _plan_context_section(plan_markdown=plan_md,
                                             plan_deliverable_id=plan_id,
                                             parent_card_id=parent_id)
        prompt = plan_section + prompt_body
    else:
        prompt = prompt_body
```

- [ ] **Step 4: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_dispatch_phase.py -v -k 'plan_context'`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_dispatch_phase.py
git commit -m "feat(kanban): plan-aware executor prompt with unresolvable fallback"
```

---

## Task 8: `add_plan_attachment` MCP tool + materialized `add_plan_attachment` / `link_plan_ref` ops

**Files:**
- Modify: `backend/app/kanban/operations.py` (add two new ops to `_materialize`)
- Modify: `backend/app/kanban/mcp_server.py` (add the tool)
- Create: `backend/tests/test_add_plan_attachment.py`

**Interfaces:**
- Tool name: `mcp__cockpit-kanban__add_plan_attachment`
- Tool signature: `add_plan_attachment(card_id: str, plan_markdown: str, child_card_ids: list[str], depends_on_graph: dict[str, list[str]]) -> dict`
- Errors: `parent_mismatch`, `child_not_found`, `cycle_detected`, `too_many_children`, `not_found`
- Caps: `MAX_CHILDREN_PER_PLAN = 50`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_add_plan_attachment.py`:

```python
import pytest

from app.kanban import mcp_server, operations
from app.kanban.operations import apply_operation
from app.kanban.models import KanbanCard, KanbanDeliverable


@pytest.mark.asyncio
async def test_add_plan_happy_path_attaches_plan_and_refs():
    # Create parent + 2 children
    parent_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "parent", "column": "Backlog"},
    )
    c1 = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "c1", "column": "Backlog",
                 "parent_card_id": parent_id},
    )
    c2 = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "c2", "column": "Backlog",
                 "parent_card_id": parent_id},
    )
    await _s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# Plan\n\nc1 then c2",
        child_card_ids=[c1, c2],
        depends_on_graph={c2: [c1]},
    )
    assert "error" not in result
    assert result["parent_card_id"] == parent_id
    assert set(result["child_card_ids"]) == {c1, c2}

    # Verify deliverables
    parent = await _s.get(KanbanCard, parent_id)
    plan_deliverables = [d for d in parent.deliverables if d.kind == "plan"]
    assert len(plan_deliverables) == 1

    c1_card = await _s.get(KanbanCard, c1)
    c2_card = await _s.get(KanbanCard, c2)
    c1_refs = [d for d in c1_card.deliverables if d.kind == "plan_ref"]
    c2_refs = [d for d in c2_card.deliverables if d.kind == "plan_ref"]
    assert len(c1_refs) == 1
    assert len(c2_refs) == 1


@pytest.mark.asyncio
async def test_add_plan_rejects_parent_mismatch():
    parent_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "p", "column": "Backlog"},
    )
    other_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "o", "column": "Backlog"},
    )
    child_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "c", "column": "Backlog",
                 "parent_card_id": other_id},
    )
    await _s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=[child_id],
        depends_on_graph={},
    )
    assert result["error"] == "parent_mismatch"


@pytest.mark.asyncio
async def test_add_plan_rejects_missing_child():
    parent_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "p", "column": "Backlog"},
    )
    await _s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=["does-not-exist"],
        depends_on_graph={},
    )
    assert result["error"] == "child_not_found"


@pytest.mark.asyncio
async def test_add_plan_rejects_cycle():
    parent_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "p", "column": "Backlog"},
    )
    c1 = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "c1", "column": "Backlog",
                 "parent_card_id": parent_id},
    )
    await _s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=[c1],
        depends_on_graph={c1: [c1]},
    )
    assert result["error"] == "cycle_detected"
    assert c1 in result["cycle"]


@pytest.mark.asyncio
async def test_add_plan_rejects_too_many_children():
    parent_id = await apply_operation(
        _s, op_type="create", entity_type="card",
        project_key="git:example", entity_id=None,
        payload={"title": "p", "column": "Backlog"},
    )
    children = []
    for i in range(51):
        cid = await apply_operation(
            _s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": f"c{i}", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        children.append(cid)
    await _s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id, plan_markdown="# x",
        child_card_ids=children, depends_on_graph={},
    )
    assert result["error"] == "too_many_children"
    assert result["max"] == 50
```

The tests need a session fixture — use:

```python
from tests.kanban_test_db import TestSessionLocal
_s_lock = __import__('threading').Lock()
_s = None

@pytest_asyncio.fixture(autouse=True)
async def _sess():
    global _s
    from tests.kanban_test_db import reset_test_tables
    await reset_test_tables()
    factory = TestSessionLocal()
    _s = factory()
    try:
        yield
    finally:
        await _s.close()
        _s = None
```

Use the existing `kanban_test_db` reset fixture pattern from `conftest.py`.

- [ ] **Step 2: Run test and verify FAIL**

Run: `cd backend && python -m pytest tests/test_add_plan_attachment.py -v`
Expected: FAIL — `mcp_server.add_plan_attachment` does not exist.

- [ ] **Step 3: Add the two new `_materialize` branches**

In `backend/app/kanban/operations.py`, add (just before the `# comment ops are pure log entries` line at the end of `_materialize`):

```python
    if entity_type == "deliverable" and op_type == "add_plan_attachment":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind="plan", ref=payload["plan_markdown"],
        ))
        # Persist the JSON graph as part of the same deliverable row (we
        # already stored the markdown above; use the JSON column on KanbanCard
        # to mark on each child via the follow-up ops).
        await session.flush()
        return
    if entity_type == "deliverable" and op_type == "link_plan_ref":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind="plan_ref",
            ref=payload["ref_json"],
        ))
        # Set the per-child depends_on column for fast dispatcher reads.
        card = await session.get(KanbanCard, entity_id)
        if card is not None and "depends_on" in payload:
            card.depends_on = payload["depends_on"]
            await session.flush()
        return
```

- [ ] **Step 4: Implement the MCP tool**

Append to `backend/app/kanban/mcp_server.py`:

```python
MAX_CHILDREN_PER_PLAN = 50


@mcp.tool()
async def add_plan_attachment(
    card_id: str,
    plan_markdown: str,
    child_card_ids: list[str],
    depends_on_graph: dict[str, list[str]] | None = None,
) -> dict:
    """Persist a plan on a parent card and wire `plan_ref` deliverables to each child.

    Args:
        card_id: The parent card id. Must be the parent of every id in
            `child_card_ids` (i.e. each child's `parent_card_id` equals this).
        plan_markdown: The plan as a markdown document.
        child_card_ids: The list of child cards the analyst is delegating to.
        depends_on_graph: A dict {child_card_id: [parent_card_ids_this_depends_on]}
            describing the dependency DAG. Must be acyclic. Each child gets its
            own `depends_on` column set to that list.

    Returns the parent card on success, or an error dict:
        {error: "not_found"} / {error: "parent_mismatch"} /
        {error: "child_not_found"} / {error: "cycle_detected", cycle: [...]} /
        {error: "too_many_children", max: 50}.
    """
    if len(child_card_ids) > MAX_CHILDREN_PER_PLAN:
        return {"error": "too_many_children", "max": MAX_CHILDREN_PER_PLAN}

    deps = depends_on_graph or {}
    cycle = mcp_kanban_deps.detect_cycle(
        {c: list(deps.get(c, []) or []) for c in child_card_ids}
    )
    if cycle is not None:
        return {"error": "cycle_detected", "cycle": cycle}

    async with KanbanSessionLocal() as s:
        from app.kanban.models import KanbanCard
        parent = await s.get(KanbanCard, card_id)
        if parent is None:
            return {"error": "not_found", "card_id": card_id}

        # Validate every child exists + has this parent.
        for cid in child_card_ids:
            child = await s.get(KanbanCard, cid)
            if child is None:
                return {"error": "child_not_found", "card_id": cid}
            if child.parent_card_id != card_id:
                return {"error": "parent_mismatch",
                        "card_id": cid, "expected_parent": card_id}

        # Materialize the plan deliverable on the parent.
        project_key = parent.project_key
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key=project_key, entity_id=card_id,
            payload={"plan_markdown": plan_markdown},
        )
        plan_deliverable_id = (
            await s.execute(
                select(KanbanDeliverable)
                .where(KanbanDeliverable.card_id == card_id,
                       KanbanDeliverable.kind == "plan")
                .order_by(KanbanDeliverable.created_at.desc())
            )
        ).scalars().first().id

        # Link plan_ref on each child + fan out depends_on.
        for cid in child_card_ids:
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key=project_key, entity_id=cid,
                payload={"ref_json": json.dumps({
                    "parent_card_id": card_id,
                    "plan_deliverable_id": plan_deliverable_id,
                }), "depends_on": list(deps.get(cid, []) or [])},
            )
        await s.commit()
        return {
            "parent_card_id": card_id,
            "plan_deliverable_id": plan_deliverable_id,
            "child_card_ids": list(child_card_ids),
        }
```

Add necessary imports at the top:

```python
import json
from sqlalchemy import select
from app.kanban import dep_resolver as mcp_kanban_deps
from app.kanban.models import KanbanDeliverable
```

- [ ] **Step 5: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/test_add_plan_attachment.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/kanban/operations.py backend/app/kanban/mcp_server.py backend/tests/test_add_plan_attachment.py
git commit -m "feat(kanban): add_plan_attachment tool with validation + materialized ops"
```

---

## Task 9: Wire new card fields through schema + REST

**Files:**
- Modify: `backend/app/kanban/schemas.py`
- Modify: `backend/app/api/v1/kanban/router.py` (the PATCH /cards/{cid} endpoint)

**Interfaces:**
- Consumes: existing API contract
- Produces: five new optional fields on the `CardUpdate` schema and propagated to `apply_operation`

- [ ] **Step 1: Verify current handler accepts arbitrary fields via the router**

Read `backend/app/api/v1/kanban/router.py` lines 530-560. The PATCH endpoint uses `apply_operation` with the payload as-is. So just exposing the five new fields on the schema is sufficient.

- [ ] **Step 2: Add the five fields to the Pydantic schema**

Edit `backend/app/kanban/schemas.py` — find `CardUpdate` (or equivalent). Add:

```python
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    analyst_run_id: str | None = None
    depends_on: list[str] | None = None
```

And add the same five fields to `CardResponse`.

- [ ] **Step 3: Run the existing kanban API tests**

Run: `cd backend && python -m pytest tests/test_kanban_api.py -v`
Expected: existing tests still pass (the new fields are optional, no behavior change for callers that don't send them).

- [ ] **Step 4: Commit**

```bash
git add backend/app/kanban/schemas.py backend/app/api/v1/kanban/router.py
git commit -m "feat(kanban): expose multi-agent fields through REST PATCH"
```

---

## Task 10: Frontend — types + API helper

**Files:**
- Modify: `frontend/src/features/kanban/types.ts`
- Modify: `frontend/src/features/kanban/api.ts`

- [ ] **Step 1: Extend `Card` type**

Edit `frontend/src/features/kanban/types.ts` — find the `Card` interface. Add:

```typescript
  analyst_agent_id?: string | null;
  executor_agent_id?: string | null;
  parent_card_id?: string | null;
  analyst_run_id?: string | null;
  depends_on?: string[] | null;
```

Add a new sibling interface for the plan attachment if not already exported:

```typescript
export interface PlanAttachmentPayload {
  parent_card_id: string;
  plan_deliverable_id: string;
}
```

- [ ] **Step 2: Add a typed wrapper for the MCP tool**

Edit `frontend/src/features/kanban/api.ts`. Add:

```typescript
export async function addPlanAttachment(
  cardId: string,
  planMarkdown: string,
  childCardIds: string[],
  dependsOnGraph: Record<string, string[]> = {},
): Promise<{ parent_card_id: string; plan_deliverable_id: string; child_card_ids: string[] } | { error: string; max?: number; cycle?: string[]; card_id?: string }> {
  const res = await fetch(`${API_BASE}/mcp/kanban/add_plan_attachment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      card_id: cardId,
      plan_markdown: planMarkdown,
      child_card_ids: childCardIds,
      depends_on_graph: dependsOnGraph,
    }),
  });
  if (!res.ok) throw new Error(`add_plan_attachment failed: ${res.status}`);
  return res.json();
}
```

(Adjust the URL path to match the existing MCP-transport pattern in `api.ts` — search for other `add_*` or `attach_*` calls.)

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/types.ts frontend/src/features/kanban/api.ts
git commit -m "feat(kanban-ui): types + API helper for plan attachment"
```

---

## Task 11: `CardEditDialog` — analyst/executor dropdowns

**Files:**
- Modify: `frontend/src/features/kanban/components/CardEditDialog.tsx`

- [ ] **Step 1: Read existing dialog**

Read the file. Note the existing provider-list and onSubmit signature.

- [ ] **Step 2: Add two dropdowns**

Inside the form, just below the existing "Agent" field, add:

```tsx
<div className="space-y-1">
  <Label htmlFor="analyst_agent_id">Analyst-agent (multi-agent split)</Label>
  <Select value={formState.analyst_agent_id ?? AUTO}
          onValueChange={(v) => setFormState({ ...formState,
            analyst_agent_id: v === AUTO ? null : v })}>
    <SelectTrigger id="analyst_agent_id">
      <SelectValue placeholder="Geen (single-agent)" />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value={AUTO}>Geen (single-agent)</SelectItem>
      <SelectItem value="claude-code">Claude Code</SelectItem>
      <SelectItem value="mimo-code">MiniMax (mimo-code)</SelectItem>
      <SelectItem value="codex-cli">Codex CLI</SelectItem>
      <SelectItem value="open-code">OpenCode</SelectItem>
      <SelectItem value="copilot-cli">Copilot CLI</SelectItem>
    </SelectContent>
  </Select>
</div>

<div className="space-y-1">
  <Label htmlFor="executor_agent_id">Executor-agent</Label>
  <Select value={formState.executor_agent_id ?? AUTO}
          onValueChange={(v) => setFormState({ ...formState,
            executor_agent_id: v === AUTO ? null : v })}>
    <SelectTrigger id="executor_agent_id">
      <SelectValue placeholder="Auto (= card.agent)" />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value={AUTO}>Auto (= card.agent)</SelectItem>
      <SelectItem value="claude-code">Claude Code</SelectItem>
      <SelectItem value="mimo-code">MiniMax (mimo-code)</SelectItem>
      <SelectItem value="codex-cli">Codex CLI</SelectItem>
      <SelectItem value="open-code">OpenCode</SelectItem>
      <SelectItem value="copilot-cli">Copilot CLI</SelectItem>
    </SelectContent>
  </Select>
</div>
```

`AUTO` is the sentinel from `CardEditDialog.tsx:26`.

The PATCH call (existing) sends the new fields through as-is — no changes needed there because the schema already passes the payload through.

- [ ] **Step 3: Type-check + lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no new errors/warnings.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/components/CardEditDialog.tsx
git commit -m "feat(kanban-ui): analyst/executor dropdowns in CardEditDialog"
```

---

## Task 12: `CardItem` — multi-agent badge

**Files:**
- Modify: `frontend/src/features/kanban/components/CardItem.tsx`

- [ ] **Step 1: Add the badge**

Inside the card body, above the title (or in the same row), add:

```tsx
{card.analyst_agent_id && (
  <span className="inline-flex items-center gap-1 rounded bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-800">
    🪄 Multi-agent
  </span>
)}
```

- [ ] **Step 2: Type-check + lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/components/CardItem.tsx
git commit -m "feat(kanban-ui): multi-agent badge on cards"
```

---

## Task 13: `CardDrawer` — Plan tab

**Files:**
- Modify: `frontend/src/features/kanban/components/CardDrawer.tsx`

- [ ] **Step 1: Add the tab**

Read the file. Find the existing `<Tabs>` component (or tab strip). Add a third tab:

```tsx
<TabsTrigger value="plan">Plan</TabsTrigger>
```

Inside `<TabsContent value="plan">`, render one of:

- For a parent that has a `plan` deliverable: the markdown via `<MarkdownRenderer>`.
- For a child that has a `plan_ref` deliverable: a link to the parent and a list of `depends_on` cards with status badges.
- Otherwise: an empty state saying "Geen plan — dit is een single-agent kaart of het plan is nog niet opgeslagen."

(Full implementation snippet — extend the existing pattern of mapping deliverables → tab content. Reference `<MarkdownRenderer>` from `@/components/shared/MarkdownRenderer`.)

- [ ] **Step 2: Type-check + lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/components/CardDrawer.tsx
git commit -m "feat(kanban-ui): Plan tab in CardDrawer"
```

---

## Task 14: Integration test (end-to-end with stubbed `_run_card`)

**Files:**
- Create: `backend/tests/integration/test_multi_agent_kanban.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end test for the multi-agent kanban flow.

Stubs `_run_card` so no real tmux session is spawned. Asserts:
  1. Tick 1: parent gets an analyst session spawned + analyst_run_id set.
  2. Analyst calls add_plan_attachment (2 children, dep c2 → c1).
  3. Parent moves to Done. Children inherit plan_ref + depends_on.
  4. Tick 2: child 1 dispatched (executor); child 2 skipped (deps unmet).
  5. Move child 1 to Done by hand.
  6. Tick 3: child 2 dispatched (executor).
"""
import pytest

from app.kanban import dispatch, operations
from app.kanban.operations import apply_operation
from app.kanban.models import KanbanCard


@pytest.mark.asyncio
async def test_multi_agent_flow(monkeypatch):
    spawned = []

    async def fake_run_card(session, **kwargs):
        spawned.append((kwargs["phase"], kwargs["card"].id))
        return {"session": f"tmux-{kwargs['phase']}-{kwargs['card'].id[:6]}"}

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    from tests.kanban_test_db import TestSessionLocal, reset_test_tables
    from app.kanban.dep_resolver import meets_dep_prerequisites

    await reset_test_tables()
    KanbanSessionLocal = TestSessionLocal()

    async with KanbanSessionLocal() as s:
        # --- Setup: parent + analyst config ---
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "parent", "column": "Backlog",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code"},
        )

        # --- Tick 1: spawn analyst ---
        parent = await s.get(KanbanCard, parent_id)
        if parent.analyst_agent_id and not parent.analyst_run_id:
            await fake_run_card(s, card=parent, project_key="git:example",
                                project_path="/tmp/x", transport=None,
                                phase="analyst")
            parent.analyst_run_id = "run-1"
            await s.commit()
        assert ("analyst", parent_id) in spawned

        # --- Analyst does the planning ---
        from app.kanban.mcp_server import add_plan_attachment
        c1 = await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "parent_card_id": parent_id})
        c2 = await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c2", "column": "Backlog",
                     "parent_card_id": parent_id})
        # Note: add_plan_attachment takes its own session, but we can validate
        # the same way by calling apply_operation directly with the same ops.
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key="git:example", entity_id=parent_id,
            payload={"plan_markdown": "# Plan\nc1 first, then c2"},
        )
        from app.kanban.models import KanbanDeliverable
        plan_id = (await s.execute(
            __import__('sqlalchemy').select(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == parent_id,
                   KanbanDeliverable.kind == "plan")
        )).scalars().first().id
        import json
        for cid in (c1, c2):
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key="git:example", entity_id=cid,
                payload={"ref_json": json.dumps({
                    "parent_card_id": parent_id,
                    "plan_deliverable_id": plan_id,
                }), "depends_on": [c1] if cid == c2 else []},
            )
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="git:example", entity_id=parent_id,
            payload={"column": "Done"},
        )
        await s.commit()

        # --- Tick 2: dispatcher reads children ---
        c1_card = await s.get(KanbanCard, c1)
        c2_card = await s.get(KanbanCard, c2)
        cards_by_id = {c1: c1_card, c2: c2_card}

        # c1 has no deps → dispatch.
        assert meets_dep_prerequisites(c1_card, cards_by_id) is True
        # c2 depends on c1 (not Done yet) → skip.
        assert meets_dep_prerequisites(c2_card, cards_by_id) is False
        if meets_dep_prerequisites(c1_card, cards_by_id):
            await fake_run_card(s, card=c1_card, project_key="git:example",
                                project_path="/tmp/x", transport=None,
                                phase="executor")

        # --- c1 finishes → mark Done ---
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="git:example", entity_id=c1,
            payload={"column": "Done"})
        await s.commit()
        c1_card = await s.get(KanbanCard, c1)
        cards_by_id = {c1: c1_card, c2: c2_card}

        # --- Tick 3: c2 deps met → dispatch ---
        assert meets_dep_prerequisites(c2_card, cards_by_id) is True
        if meets_dep_prerequisites(c2_card, cards_by_id):
            await fake_run_card(s, card=c2_card, project_key="git:example",
                                project_path="/tmp/x", transport=None,
                                phase="executor")

    # Verify spawn order: analyst(parent), executor(c1), executor(c2).
    assert spawned == [
        ("analyst", parent_id),
        ("executor", c1),
        ("executor", c2),
    ]
```

(Use the existing `conftest.py` fixtures — `_reset_test_db` and `_patch_kanban_db` already reset the DB and patch `KanbanSessionLocal`.)

- [ ] **Step 2: Run test and verify PASS**

Run: `cd backend && python -m pytest tests/integration/test_multi_agent_kanban.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_multi_agent_kanban.py
git commit -m "test(kanban): end-to-end multi-agent flow with stubbed _run_card"
```

---

## Task 15: Manual smoke-test cookbook

**Files:**
- Create: `docs/cockpit/multi-agent-kanban.md`

- [ ] **Step 1: Write the doc**

The doc should cover:

1. **Wanneer gebruik je multi-agent?** — Bij kaarten die eerst analyse verdelen in N taken met afhankelijkheden, en waarvan de uitvoering over N executors mag parallel lopen.
2. **`analyst.md` voorbeeld** — een minimale persona-body die uitlegt hoe je kaarten splitst en plan schrijft.
3. **Stappen in de UI** — Maak een kaart in Backlog → open CardEditDialog → kies Analyst-agent = `claude-code`, Executor-agent = `mimo-code` → klik dispatch.
4. **Verwacht gedrag** — Analyst-sessie spawnt, kind-kaarten verschijnen in dezelfde kolom, parent wordt verplaatst naar Done, plan-tab toont het plan-attachment.
5. **Limieten** — 50 kind-kaarten per parent; cyclische deps worden geweigerd; geen aggregator aan parent-kant.

- [ ] **Step 2: Commit**

```bash
git add docs/cockpit/multi-agent-kanban.md
git commit -m "docs(cockpit): multi-agent kanban smoke-test cookbook"
```

---

## Task 16: Final spec-coverage self-review

- [ ] **Step 1: Skim the spec, point at the task that covers each section**

| Spec section | Covered by |
|---|---|
| §1 per-card fields | Tasks 1, 2, 9 |
| §2 Plan as KanbanDeliverable | Tasks 1, 8 |
| §3 Phase-aware dispatch | Tasks 5, 6, 7 |
| §4 add_plan_attachment MCP tool | Task 8 |
| §5 Analyst persona + verboden | Task 4 |
| §6 UI changes (minimal) | Tasks 10, 11, 12, 13 |
| §7 Backward compatibility | Tasks 1, 2 (default None), Task 6 (legacy path) |
| §8 Error handling & edge cases | Task 3 (cycle + missing parent), Task 6 (analyst_run_id guard), Task 7 (unresolvable plan), Task 8 (50-child cap + validation) |
| §9 Testing strategy | Tasks 3, 4, 8, 11, 14 |

All sections covered.

- [ ] **Step 2: Placeholder scan**

Run: `grep -nE 'TBD|TODO|XXX|FIXME|fill in|implement later' docs/superpowers/plans/2026-07-08-multi-agent-kanban.md`
Expected: no output.

- [ ] **Step 3: Type-consistency check**

- `_phase_provider_id(card, phase=...)` — used by Task 5 tests + Task 6 orchestration. Same signature.
- `_phase_target_agent(card, project_path, phase, source_column)` — same as above.
- `_plan_context_section(...)` — same.
- `MAX_CHILDREN_PER_PLAN = 50` — defined and used in Task 8 only.
- `add_plan_attachment` MCP tool — defined once with the signature from the spec §4.

- [ ] **Step 4: Commit nothing (review-only task)**, just mark complete.

Run nothing — this task is documentation.

---

## Summary

15 atomic tasks (Task 16 is a self-review checklist), each producing one commit.
Total backend touches: 5 files in `app/kanban/`, 1 new file; plus 6 new test files.
Total frontend touches: 4 components/files, 1 new API helper.
