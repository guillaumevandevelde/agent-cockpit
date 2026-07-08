# Multi-agent kanban workflow — design

**Date:** 2026-07-08
**Status:** Approved (design); ready for writing-plans
**Builds on:** kanban dispatch (`backend/app/kanban/dispatch.py`),
persona-as-file pattern (`docs/superpowers/specs/2026-06-15-kanban-agents-design.md`),
column-platform routing (`66df2d6` — "let a column pick which subscription its cards spawn against").

## Problem

The kanban board today dispatches a single Claude session per card. For cards that need
genuine decomposition — "build X with these sub-steps in this order" — the engineer
session has to do analysis + execution in one shot, with no enforced pause between the
two and no schema for splitting work across multiple sessions.

Users with access to two different subscriptions (e.g. Anthropic sonnet 5 for analysis,
MiniMax M3 for execution) cannot route a single card to two different agents; the
existing `card.agent` field is overloaded (provider-id or persona-name) and there is
no concept of a "phase".

## Goals

- A kanban card can opt into a **two-phase workflow**: analyst first, then executors.
- Per-card configuration: `analyst_agent_id` + `executor_agent_id`. Provider-id only in
  this iteration (`claude-code`, `mimo-code`, `codex-cli`, `open-code`, `copilot-cli`).
- Analyst session produces a **plan** + spawns **child cards** on the board with
  `parent_card_id` and `depends_on` set. Parent moves to `Done` once the plan is saved.
- Dispatcher auto-respects the dependency graph: a child card is only dispatched once
  every card it depends on is in `Done`.
- Plan lives on the parent card as a `KanbanDeliverable(kind="plan")`. Each child card
  gets a `KanbanDeliverable(kind="plan_ref")` pointing to it, so the executor session
  finds it via the prompt context.
- Fully **backward compatible**: cards without `analyst_agent_id` behave exactly as
  today. No migration, no schema break, no column changes.
- Optional / opt-in: a project that only has one subscription available simply never
  sets the new fields, and the existing flow runs unchanged.

## Non-goals (this iteration)

- Parallel analyst sessions for one parent (always 1 analyst per parent).
- An aggregator session that summarises child-card deliverables back onto the parent
  (parent is already `Done` after planning).
- Cost or time budgets on the analyst (no cap on session length; will be added when
  usage data exists).
- Persona support in `analyst_agent_id` / `executor_agent_id` (provider-id only;
  persona is fixed to `analyst` for the analyst phase).
- Cross-project planning (always within one project).
- A dedicated "Multi-agent overview" UI screen (only CardEditDialog and drawer-tab).

## Design

### 1. Per-card fields on `KanbanCard`

| Field | Type | Default | Notes |
|---|---|---|---|
| `analyst_agent_id` | `String(64) \| None` | `None` | Provider-id. `None` = legacy single-agent flow. |
| `executor_agent_id` | `String(64) \| None` | falls back to `card.agent` | Provider-id for the executor phase. |
| `parent_card_id` | `String \| None` | `None` | FK to parent; set by `create_card` when invoked from analyst. Indexed. |
| `analyst_run_id` | `String \| None` | `None` | Set **before** spawning the analyst session; the next tick uses it to skip duplicate spawns. |
| `depends_on` | `JSON \| None` | `None` | List of card-IDs that must be `Done` before this child is dispatchable. Set by analyst at create time. |

`card.agent` keeps its current overloaded role (provider or persona) for the **single-agent
fallback path**; the new fields are purely additive.

### 2. Plan as a `KanbanDeliverable`

Two new `kind` discriminator values on the existing `KanbanDeliverable` table:

| `kind` | Where it lives | Contents |
|---|---|---|
| `plan` | Parent card | Markdown plan + JSON front-matter with `child_card_ids` and `depends_on_graph` |
| `plan_ref` | Each child card | `{"parent_card_id": "...", "plan_deliverable_id": "..."}` |

Single source of truth on the parent; children reference it. Executors see the plan in
their prompt preamble without an extra MCP round-trip.

No new table, no new column-flow. Only the `kind` column on `KanbanDeliverable` gets
two new accepted string values, folded via the existing `apply_operation` pipeline.

### 3. Phase-aware dispatch (`backend/app/kanban/dispatch.py`)

`_run_card(card, *, phase="executor")` becomes the single entry point. The default
keeps the existing signature working:

- `phase="analyst"`: provider is `card.analyst_agent_id or "claude-code"`, persona
  resolves to `.claude/agents/analyst.md` (or fallback).
- `phase="executor"`: provider is `card.executor_agent_id or card.agent or "claude-code"`,
  persona follows today's `_persona_for(card)` logic.

`_run_card_dispatch_tick` is updated to:

```python
for card in dispatchable_cards():
    if not meets_dep_prerequisites(card):      # NEW
        continue
    if card.analyst_agent_id and not card.analyst_run_id:
        await _run_card(card, phase="analyst")
        card.analyst_run_id = run_id          # persisted via apply_operation
        continue
    await _run_card(card, phase="executor")
```

`analyst_run_id` is set **before** the spawn is awaited. The follow-up tick sees it
and skips. No mutex needed because the column update is atomic via `apply_operation`.

`meets_dep_prerequisites(card)` reads `card.depends_on` and fails while any referenced
card is not in `Done`. No deps → returns `True` immediately.

`build_executor_prompt(card, plan_ref)` resolves the plan via `plan_ref` and prepends
a "PLAN CONTEXT — read this first" section. If the ref is unresolvable, the prompt
includes a `report_impediment` hint and the executor is expected to surface it.

### 4. New MCP tool: `mcp__cockpit-kanban__add_plan_attachment`

Signature:

```python
{
  "card_id": "<parent-kaart ID>",          # must be the parent
  "plan_markdown": "...",
  "child_card_ids": ["c1", "c2", "c3"],
  "depends_on_graph": {"c2": ["c1"], "c3": ["c1", "c2"]}
}
```

`depends_on_graph` is the analyst's authoritative description of the dependencies.
The server fans it out: for each child, its `depends_on` column is set to the
referenced list (`c1` → `[]`, `c2` → `["c1"]`, `c3` → `["c1", "c2"]`). The
dispatcher reads `depends_on` directly from each child card; it does **not** have
to walk the parent's plan-deliverable at dispatch time. The plan-deliverable
keeps the full graph in its front-matter for the human reader and the audit
trail.

Server-side validation:

- `card_id` is the parent of every id in `child_card_ids` (`parent_mismatch` error).
- Every `child_card_id` exists (`child_not_found` error).
- `depends_on_graph` is acyclic (`cycle_detected` error, payload names the cycle).
- Total children ≤ 50 (hard cap; `too_many_children` error, payload includes `max: 50`).
  The cap exists to keep a single analyst session from spamming the board.

On success the server emits two new `KanbanOp` operations:
`add_plan_attachment` (parent side) and `link_plan_ref` (each child side), each folded
through the existing `apply_operation` machinery.

### 5. Analyst persona

**Convention first, fallback second:**

- If `.claude/agents/analyst.md` exists in the project, its body is used as the analyst
  persona — same as today's engineer pattern.
- Otherwise the server falls back to a hard-coded prompt in
  `backend/app/kanban/analyst_prompt.py`.

The fallback prompt is intentionally restrictive:

```
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
```

The "Verboden" block is the hard boundary that prevents the analyst from doing the
whole task in one session and leaving empty children behind.

### 6. UI changes (minimal)

- `CardEditDialog`: two new dropdowns — Analyst-agent and Executor-agent — using the
  existing provider-id list. Defaults: "Geen" / "Auto (= card.agent)".
- `CardItem`: small `🪄 Multi-agent` badge when `analyst_agent_id` is set.
- `CardDrawer`: new "Plan" tab. For a parent: renders the `plan` deliverable as
  markdown (`<MarkdownRenderer>`). For a child: shows the `plan_ref` link to the
  parent and the dependency status of each `depends_on` card.
- `Board` and `KanbanPage`: no changes. Polling + drag/drop continue to work
  unchanged because the schema is purely additive.

No new column. No new screen. No new navigation entry.

### 7. Backward compatibility

- Kaarten zonder `analyst_agent_id` slaan het multi-agent-pad volledig over:
  `_run_card_dispatch_tick` ziet de conditie `card.analyst_agent_id and not card.analyst_run_id`
  als `False` en valt door op de bestaande executor-tak.
- `meets_dep_prerequisites` retourneert direct `True` voor kaarten zonder `depends_on`,
  wat voor alle bestaande kaarten het geval is.
- `_run_card(card)` met default `phase="executor"` is signature-compatibel met de
  huidige aanroepers (`scheduler.py`, `dispatch.py:redispatch_card`,
  `mcp_server.py:redispatch_card`).
- `KanbanCard` heeft vier nieuwe kolommen met `None` default — bestaande rijen in de
  kanban-DB blijven zonder migratie geldig.

### 8. Error handling & edge cases

| Edge case | Gedrag |
|---|---|
| Analyst sessie crasht halverwege | `analyst_run_id` blijft staan; gebruiker kan `redispatch_card` aanroepen. Bij deels aangemaakte kind-kaarten: dispatcher dispatched de kinderen die klaar zijn, waarschuwing in activity-feed. |
| Cyclische `depends_on` | `add_plan_attachment` weigert met `cycle_detected` + cycle-lijst. |
| Kind-kaart dispatched zonder resolvable plan_ref | `build_executor_prompt` includeert placeholder + `report_impediment`-hint. Geen crash, geen data-verlies. |
| Parent verwijderd terwijl kinderen bezig zijn | Kinderen verliezen `parent_card_id`; deps-resolutie valt terug op "geen parent = geen deps = door". Eenmalige waarschuwing in activity-feed per kind. |
| Onbekende `analyst_agent_id` / `executor_agent_id` | Spawn faalt; activity-feed logt; kaart blijft ongedispatched. Geen fallback. |
| >50 kind-kaarten per parent | `add_plan_attachment` weigert met `too_many_children`. |
| Race tussen twee analyst-spawns | `analyst_run_id` guard via atomaire kolom-update. |

### 9. Testing strategy

**Unit tests** (`backend/tests/kanban/`):

| File | Coverage |
|---|---|
| `test_dispatch_phase.py` | `_run_card` kiest juiste provider per phase; `analyst_run_id` wordt gezet vóór spawn; tweede tick ziet 'm en slaat over (mocked spawn). |
| `test_dep_resolver.py` | `meets_dep_prerequisites` faalt tot alle parents Done; slaagt daarna; geen parents = door. |
| `test_add_plan_attachment.py` | Parent-mismatch, child_not_found, cycle_detected, too_many_children. |
| `test_models.py` | Nieuwe velden round-trippen via `apply_operation`. |
| `test_analyst_prompt.py` | Fallback-prompt bevat de "Verboden" + "Werkwijze" secties. |

**Integration test** (`backend/tests/integration/`):

End-to-end met stubbed `_run_card` (geen echte tmux):

1. Multi-agent-kaart in Backlog, `analyst_agent_id=claude-code`,
   `executor_agent_id=mimo-code`.
2. Tick 1: spawn analyst.
3. Stub-analyst doet `add_plan_attachment` (2 kinderen + 1 dep), parent → Done.
4. Tick 2: kind-1 dispatched; kind-2 wacht op kind-1.
5. Kind-1 → Done handmatig.
6. Tick 3: kind-2 dispatched.

**Manual smoke** (in `docs/cockpit/`, nieuwe sectie "Multi-agent kanban"):

- Voorbeeld `analyst.md` inhoud.
- Stappen om een multi-agent-kaart aan te maken in de UI.
- Verwacht bordgedrag (kind-kaarten in dezelfde kolom, dep-volgorde, plan-tab).

**CI**: bestaande `quality.yml` pakt nieuwe unit + integration tests automatisch op.
Geen nieuwe workflow nodig.

### 10. Out-of-scope acknowledgements

- Geen parallelle analyst per parent — sequentieel kind-kaarten zijn voldoende voor
  deze iteratie. Schaalt naar N als planner later toegevoegd wordt.
- Geen cost/time budget — wordt toegevoegd zodra we usage-data hebben.
- Geen persona-ondersteuning in de nieuwe velden — provider-only. Bewuste keuze om
  de feature niet te mengen met de bestaande persona-overload in `card.agent`.
- Geen cross-project planning — dispatcher blijft per-project.

## Open questions (none — all resolved during brainstorming)

- ~~Workflow-vorm~~ → twee aparte sessies, sequentieel.
- ~~Taakrepresentatie~~ → kind-kaarten op het bord met parent_link.
- ~~Config-niveau~~ → per kaart (analyst_agent_id, executor_agent_id).
- ~~Parent-levenscyclus~~ → Done na planning; kinderen zijn de uitvoering.
- ~~Dependency-dispatch~~ → auto, dispatcher wacht.
- ~~Plan-locatie~~ → plan-attachment op parent + plan_ref op kinderen.
- ~~Analyst-prompt~~ → gebruiker-eigen analyst.md, fallback in code.
- ~~Aanpak (data-laag)~~ → dispatch-pipeline met phase-aware router.
- ~~Edge cases~~ → zeven cases met expliciete gedragingen (zie §8).
- ~~Test-strategie~~ → unit + integratie + handmatige smoke.
