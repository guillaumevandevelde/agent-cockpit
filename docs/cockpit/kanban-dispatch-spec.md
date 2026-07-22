---
title: "Kanban auto-dispatch — spec"
type: spec
status: active
---

# Kanban auto-dispatch — spec

> **Bron van waarheid:** dit document is leidend voor de auto-dispatch-laag.
> Gerelateerde superpowers-werkdocumenten (taak-specifiek, geen tweede waarheid):
>
> - `docs/superpowers/specs/2026-06-15-kanban-agents-design.md` — persona's (Analyst/Developer) + per-project shipmode.
> - `docs/superpowers/specs/2026-06-29-kanban-dispatch-transport-design.md` — abstractie van het spawn-transport (tmux vandaag, podman later).
> - `docs/superpowers/plans/2026-06-15-kanban-dispatch-agents.md` — TDD-implementatieplan dat bovenstaande heeft uitgevoerd.
> - `docs/superpowers/specs/2026-07-10-kanban-model-override-design.md` — per-kaart/per-kolom model-override (`card.model`/`column.default_model`) bovenop de persona-routing.
>
> Zie `00-orientation.md` → *Documenten* voor de drie-bomen-regel.

Turns the **passive** board (v1) into an **active** one: a reliable poll-loop
("the dispatcher") watches the **Todo** column of *auto-dispatch-enabled* projects
and, for each unclaimed card, **claims it as the session that will work it**, moves
it to **Doing**, and spawns a Claude Code session (tmux today, podman later) with the
card as its opening task. The session shows up in CC Bridge; the card's `claimed_by`
label *is* that session, so the board and the bridge are linked without a join table.

This is the "kanban as hoofdwerking" layer that was deferred from the board v1
(see `kanban-followups.md` → *push-on-idle / initiative layer*).

## Design decisions (agreed)

- **Trigger = polling, not hooks.** An APScheduler interval job (the existing
  `scheduler_service`) ticks every ~10 s. Polling is the most reliable process:
  it survives restarts, depends on no event plumbing, and always re-reads real
  board state. Idempotency comes from the claim, below.
- **Claimant = the session.** The dispatcher **claims first, spawns second**.
  The claim is the existing conditional first-wins op, so two ticks (or two
  devices) racing the same card produce exactly one winner; the loser skips.
  The claimant label is `agent:<session_name>` — the running tmux session — so a
  card in Doing points at exactly the session doing the work.
- **Auto-Doing.** On claim the card moves straight to **Doing**, so the board
  reflects that work has started.
- **Scope = git worktree.** Sessions spawn with `mode="worktree"` via the existing
  `services/runs/spawn.py`, isolating each card's work.
- **Transport is abstracted.** The dispatcher calls a `SpawnTransport` callable;
  today it wraps `spawn_session` (tmux). Swapping to the rootless-podman wrapper
  later touches only the transport, not the dispatcher.
- **Auto-dispatch is opt-in and device-local, separate from "kanban enabled".**
  Merely enabling the board must never start spawning sessions. Enablement is a
  `KanbanMeta` key (`autodispatch:<project_key>`); `KanbanMeta` is not part of the
  synced op-log, so each device decides for itself whether *it* spawns.
- **Concurrency cap = 1 active card per project.** The dispatcher skips a project
  that already has an `agent:`-claimed card in Doing. Derivable from board state
  alone — no live-session registry needed.

## Flow (per project, per tick)

1. Skip the project if it already has a card in **Doing** claimed by `agent:%`
   (cap reached).
2. Resolve the board's `project_key` → a **local path on this device** by matching
   `resolve_project_key(path)` over the registered projects. No local match → skip
   (the board may be synced from another device that has the repo).
3. Pick the first unclaimed **Todo** card by `rank`.
4. Mint `name = "k-<slug>-<hex4>"`; `claimant = "agent:" + name`.
5. **Claim** the card as `claimant`. If the claim is rejected (lost the race), stop.
6. **Move** the card to **Doing**.
7. **Spawn** via the transport: `directory=<local path>`, `mode=worktree`,
   `session_name=name`, `prompt=<card-as-task>`.
8. **Spawn fails** → compensate: **release** the claim and move the card back to
   **Todo**, then surface the error in logs. The card is reusable next tick.

## The card-as-task prompt

The spawned session opens with a prompt that tells the agent it has already been
assigned the card and how to report back through the MCP:

```
You are an agent picking up a Kanban card from the Agent Cockpit board.
The card is already claimed by you and moved to "Doing".

# <title>
<description>

When finished: use the `cockpit-kanban` MCP tools to move the card to "Review"
(`move_card`) and attach your result (`attach_deliverable`, e.g. a branch or PR URL).
If you cannot complete it, leave a `comment` explaining why.
```

## Components

- `backend/app/kanban/dispatch.py`
  - `is_autodispatch_enabled` / `set_autodispatch` / `list_autodispatch_projects`
    (KanbanMeta-backed, device-local).
  - `build_card_prompt(card) -> str`.
  - `SpawnTransport` Protocol + `tmux_transport` (wraps `spawn_session`).
  - `dispatch_project(session, *, project_key, project_path, transport) -> dict | None`
    — the unit under test (claim-before-spawn, cap, compensation).
  - `run_dispatch_tick(*, transport=tmux_transport)` — resolves enabled projects to
    local paths and dispatches each; the job body.
- `backend/app/main.py` — register the interval job on startup.
- `backend/app/api/v1/kanban/router.py` — `GET/POST .../autodispatch` to read/toggle
  per-project enablement.
- `frontend/src/features/kanban/` — an "Auto-pick Todo cards" toggle on the page.

## Out of scope (follow-ups)

- **Stale-claim reaping.** A session that dies without moving the card leaves it
  claimed in Doing; v1 needs a manual release. Add a reaper (TTL or tmux-liveness)
  later.
- **Per-card / global concurrency tuning** beyond the 1-per-project cap.
- **Podman transport** (the abstraction is ready; the impl is the containerized-
  sessions track).
- **Real `claimed_by` identity for UI claims** (still `me@ui`, tracked in followups M6).

## Reporting a human-decision impediment (standard question flow)

When an agent gets stuck on something only a human can decide (Postgres vs
SQLite, scope question, etc.) it must use **`report_impediment`** with an
optional `options: list[str]` and **end its session immediately** — it must
not block on the answer. This is the standard question flow for every
human-decision request:

- MCP tool: `mcp__cockpit-kanban__report_impediment(card_id, question, options?)`
  (`backend/app/kanban/mcp_server.py:report_impediment`).
- REST: `POST /api/v1/kanban/cards/{cid}/report-impediment` (mirror).
- Behaviour: card moves to `Impediment`, claim is released, the session ends.
  When `options=` is supplied a `KanbanGate` row in status="open" is also
  created so the UI shows choice buttons; the human picks one via
  `POST /cards/{cid}/gates/{gate_id}/answer` (the existing gate path).
- The legacy blocking `open_gate` tool poll-loops on the agent session —
  this **blocks the session** until a human happens to answer, leaves the
  worktree alive during the wait, and has been the cause of "wedged session
  → worktree reaped" losses (kanban card 28b578ba). It remains in the API
  for diagnostic / in-flow micro-decisions but is **not** the recommended
  path for human-decision blockers.

When the human clicks **Resolve impediment** on an Impediment card the
dispatcher restarts a fresh session via
`POST /cards/{cid}/resolve-impediment`. `router.resolve_impediment`
composes the resumed prompt's `impediment_question` from:

1. The latest `**Impediment:** <question>` comment (set by
   `report_impediment`).
2. When a `KanbanGate` with `status="answered"` exists on the card
   (`service.latest_gate_answer`, latest-by-`answered_at`), the chosen
   option is appended as `<question>\n\nChosen answer: <option>` so the new
   agent sees both the original ask and the human's pick in the
   `## IMPEDIMENT` block of `build_card_prompt`.

A resolved Impediment card without a gate (legacy free-text
`report_impediment(question=...)`) gets the raw question back — backwards
compatible.

## Provider vs. persona: het `card.agent`-veld

> Gepromoot uit [`../superpowers/specs/2026-07-03-card-edit-provider-dropdown-design.md`](../superpowers/specs/2026-07-03-card-edit-provider-dropdown-design.md).

`card.agent` (nullable string) is bewust overladen en wordt door `dispatch.py`
gedisambigueerd via string-match tegen de bekende-provider-ids:

- een **provider-id** (`claude-code`, `open-code`, `codex-cli`, `mimo-code`) → welke CLI
  spawnt, of
- een **persona-naam** (`.claude/agents/<name>.md`) → expliciete override van de
  kolom-afgeleide persona.

De UI presenteert dit veld als een **Provider-dropdown** (zowel `CardEditDialog` als
`CardDrawer`), met een `AUTO`-sentinel die naar `null` mapt (= gebruik de globaal gekozen
provider). Beide widgets schrijven dus provider-id-vormige waarden; het edit-veld heet
**"Provider"**, niet meer het dubbelzinnige "Agent". De kolom→persona-mapping
(`ColumnSettingsDialog`'s "default agent", `dispatch._persona_for_card`) blijft ongewijzigd —
persona-selectie leeft nu in de standalone Agents-pagina, niet als tweede per-card-widget.
Een stale persona-waarde op een oude card self-healt bij de volgende save (geen migratie
nodig).

## MCP-robustness & health

> Gepromoot uit [`../superpowers/specs/2026-06-27-kanban-mcp-robustness-design.md`](../superpowers/specs/2026-06-27-kanban-mcp-robustness-design.md).

De kanban-MCP wordt via een SSE-server aan agents blootgesteld. Twee robustness-lagen:

- **Health-probe i.p.v. config-check.** `GET /api/v1/kanban/mcp-health` doet een interne
  HTTP-probe naar de SSE-mount en geeft `{healthy, latency_ms, error}` terug (incl.
  auth-header, zodat token-problemen ook zichtbaar worden). De frontend pollt dit elke 30s
  wanneer MCP enabled is en toont een **persistente** rode banner + status-dot bij
  onbereikbaarheid — geen vluchtige toast, want het oude "MCP: enabled" las de config, niet de
  realiteit (crash/token-rotatie/SSE-breuk bleef onzichtbaar).
- **Error-boundary op elke tool.** Een `_safe`-decorator vangt alle excepties, logt met
  traceback server-side, en geeft `{"error": <msg>, "type": <ClassName>}` terug i.p.v. te
  raisen — de MCP-sessie crasht nooit op een tool-fout en de agent krijgt een actionable
  gestructureerde fout. Transiente `OperationalError: database is locked` krijgt één retry na
  100ms (SQLite-contention onder concurrent dispatch).

