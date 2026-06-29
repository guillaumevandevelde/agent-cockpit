# Kanban dispatch controls + centralized project transport — design

Date: 2026-06-29
Branch: `kanban-dispatch-transport`

## Problem

Two related gaps in the Kanban auto-dispatch feature:

1. **The auto-dispatch toggle disappeared.** Commit `0a71b58` ("replace auto-pickup
   scheduler with immediate dispatch on column drop") removed `AutodispatchToggle.tsx`
   and its `api.ts` client methods, intending to switch to drop-triggered dispatch.
   But the refactor was left half-done: the polling scheduler still runs
   (`main.py:49` → `schedule_kanban_dispatch(interval_seconds=10)` → `run_dispatch_tick`),
   and that tick still gates on the `autodispatch:<project_key>=1` meta flag
   (`dispatch.py` `list_autodispatch_projects`). With no UI left to set that flag, the
   poller silently never dispatches, and — because the stale-claim **reaper** lives
   inside the same gated tick — dead `agent:` claims (e.g. `k-cleanup-test-f22a`) never
   get reaped either. Net effect: Backlog cards are only ever picked up by manually
   dragging them into an agent column.

2. **Project transport is implicit and invisible.** A card's `transport` field is
   `worktree | sandcastle | auto(null)`. "Auto" resolves at dispatch time via
   `get_transport_for_project()`, which today returns `sandcastle` **iff** a
   `SandcastleConfig` row is enabled for the project path, else `worktree`. There is no
   project-level transport control, so it is not discoverable that "auto" depends on
   whether Sandcastle happens to be enabled.

## Goals

- Restore an explicit **per-project auto-dispatch toggle** (polling model: the poller
  picks the top unclaimed Backlog card automatically while the project is under its cap).
- Add a **per-project concurrency cap** (max concurrent agent sessions). Default **4**.
- Add a **per-project "Default transport" dropdown** (`worktree | sandcastle`) as the
  single source of truth that card-transport `auto` resolves to.

## Non-goals

- Reworking the drag-to-agent-column immediate dispatch path (it stays as-is).
- Redesigning the Sandcastle config feature; we only link/surface it.
- A global (cross-project) session cap — explicitly chosen per-project only.

## Decisions

### Transport source of truth — meta-key is authoritative (Approach 1)

A new meta key `transport:<project_key>` ∈ `{worktree, sandcastle}` (default `worktree`)
becomes the authority, consistent with the existing `shipmode:` and `skip_permissions:`
meta keys. `get_transport_for_project()` reads it first instead of inferring from
`SandcastleConfig.enabled`.

To keep the two from drifting, the Default-transport setter keeps `SandcastleConfig.enabled`
in sync: choosing `sandcastle` sets `enabled=True` (creating a default config row if none
exists), choosing `worktree` sets `enabled=False`. Sandcastle detail settings remain in the
existing Sandcastle panel.

Rejected — Approach 2 (reuse `SandcastleConfig.enabled` directly as the dropdown value):
conflates "which transport" with "a sandcastle config exists", and leaves projects without a
sandcastle row no place to record an explicit `worktree` choice.

## Data model (kanban `KanbanMeta`, key/value rows)

| Key | Values | Default | Notes |
|-----|--------|---------|-------|
| `autodispatch:<project_key>` | `0` / `1` | off (no row) | Already exists; getter/setter/endpoint present. |
| `max_sessions:<project_key>` | integer string | `4` | New. Max concurrent agent sessions. |
| `transport:<project_key>` | `worktree` / `sandcastle` | `worktree` | New. Authoritative project default transport. |

No schema migration: `KanbanMeta` is already a generic key/value table.

## Backend changes

`backend/app/kanban/dispatch.py`
- Add `get_max_sessions(session, project_key) -> int` (default 4) and
  `set_max_sessions(session, project_key, n)`.
- Add `get_default_transport(session, project_key) -> str` (default `worktree`) and
  `set_default_transport(session, project_key, value)`; the setter also flips
  `SandcastleConfig.enabled` to match.
- Replace the binary `_project_is_busy(cards)` with a count:
  `_active_session_count(cards)` = cards in agent columns claimed by `agent:`. The cap
  check in `dispatch_project` becomes `if _active_session_count(cards) >= cap: return None`.
  The poller then claims successive Backlog cards in one tick until the cap is hit.
- `get_transport_for_project(project_path)`: resolve `project_key`, read
  `transport:<project_key>` meta; return `sandcastle_transport` for `sandcastle`, else a
  worktree transport honoring `skip_permissions`. (Removes the implicit SandcastleConfig
  inference as the primary signal.)

`backend/app/api/v1/kanban/router.py`
- `/autodispatch` GET/POST already exist — no change.
- Add `GET|POST /max-sessions` and `GET|POST /transport` mirroring the shipmode/skip
  endpoints, with matching request schemas in the kanban schemas module.

## Frontend changes

`frontend/src/features/kanban/api.ts`
- Re-add `getAutodispatch` / `setAutodispatch` (removed in `0a71b58`).
- Add `getMaxSessions` / `setMaxSessions` and `getDefaultTransport` / `setDefaultTransport`.

`frontend/src/features/kanban/components/`
- Re-introduce `AutodispatchToggle.tsx` (per the removed component's pattern).
- Add `MaxSessionsControl.tsx` — a small number stepper (min 1).
- Add `DefaultTransportSelect.tsx` — `worktree | sandcastle` dropdown; when `sandcastle`
  is selected, show a link to the Sandcastle config panel.

`frontend/src/features/kanban/KanbanPage.tsx`
- Render the three controls in the toolbar next to `ShipModeToggle` /
  `SkipPermissionsToggle` (`:145-146`), same compact `size="sm"` styling.

## Behavior & edge cases

- Auto-dispatch **off** → poller skips the project (current behavior); manual
  drag-to-agent-column dispatch still works.
- With auto-dispatch **on**, the reaper (already inside the tick) clears dead `agent:`
  claims, unwedging stuck cards like `k-cleanup-test-f22a`.
- Lowering the cap below the current number of live sessions → running sessions are left
  alone; no new dispatch until the active count drops below the cap.
- Default transport = `sandcastle` but no usable Sandcastle config → a clear UI warning
  rather than a silent fallback.

## Testing

Templates: `backend/tests/test_kanban_dispatch.py`, `test_kanban_shipmode.py`.
- Cap counting: busy at N active, free at N-1; poller fills up to the cap in one tick.
- `get_transport_for_project` resolves from the `transport:` meta (worktree and sandcastle
  cases); setter keeps `SandcastleConfig.enabled` in sync.
- API round-trips for `/autodispatch`, `/max-sessions`, `/transport`.
- Default cap is 4 when no `max_sessions:` row exists.
