# Kanban — known follow-ups (post-v1)

These came out of the final code review (2026-06-14). The merge-blockers were fixed
(see commits `d42cde2`, `7769d88`); the items below were consciously deferred.

## Should fix before activating sync / multi-project hardening

- **I4b — `.mcp.json` write path allowlisting.** `enable`/`disable` write `.mcp.json`
  into any directory the backend user can write, reachable via the unauthenticated,
  `0.0.0.0`-bound, open-CORS API. The atomic-write corruption risk is fixed; the
  *write-anywhere* surface is not. This matches the whole app's existing
  unauthenticated-local posture, so it's deferred — but before exposing Cockpit beyond
  localhost, validate `project_path` against the known/registered projects list.

## UX / polish

- **M4 — rank reordering in the UI.** Backend supports `rank` (LWW) and `MoveRequest.rank`,
  but the board only sends `{column}` on drop, so within-column reordering is inert and
  cards always sort by create-time HLC. Wire drag-drop to compute and send a `rank`.
- **M6 — hardcoded UI claimant `"me@ui"`.** All UI claims share one identity. Give the
  UI a real per-user/per-session claimant label.
- **M1 — `priority`/`labels` bypass LWW.** Set unconditionally in `_materialize` (fine for
  HLC-ordered replay and the live newest-tick path, but inconsistent with title/description).
  Make them LWW for uniformity if they become concurrently editable.
- **M2 — empty `update` ops.** A no-field PATCH / `update_card(id)` still appends an op and
  bumps `updated_at`, polluting the activity feed. Short-circuit when the payload is empty.

## Sync milestone — FROZEN (scaffolding pruned 2026-06-18)

Decision: **prune the dead sync seam, freeze the HLC/op-log/LWW core.** See the trade-off
in `sync-hlc-freeze-vs-prune.md`. `sync.py` (`ops_since` / `ingest_ops` / `SyncTransport` /
`LocalNoopTransport`) and its tests are removed; the HLC, op-log and per-field LWW stay as
a documented dormant core (they still power the activity feed, replay ordering and claim
arbitration). To revive sync when a 2nd device is real:

- Implement a real `SyncTransport` (Turso/libSQL embedded replica or `sqld`, or a REST
  push/pull). Re-add `ops_since` / `ingest_ops` (git history has the pruned version) and
  wire them on a schedule.
- Introduce Alembic migrations for the kanban store before a non-wipeable remote primary
  exists (deferred from v1 per the plan; materialized tables remain rebuildable via
  `rematerialize()`).
- Add the optional **push-on-idle** initiative layer (reuse the scheduled-messages delivery
  engine to inject the next card on the `Stop` hook).

## Upstream Agent Team Presets — deliberately NOT adopted (2026-07-08)

Decision: **don't port upstream's Agent Team Presets/launch-orchestration/second Agent
Mail MCP shim** — see the trade-off in `upstream-agent-teams-decision.md`. It's a
competing orchestration paradigm to our kanban-dispatch + kanban-based Agent Mail, not a
complement. Only the universal provider-correctness bugs it surfaced were cherry-picked
(Codex `reasoning_effort` support, the Bedrock-env fallthrough in `platform_env.py`,
explicit OpenCode rejection of `reasoning_effort`).

**If you're picking up the "Agent Bridge UI-cluster" card (team lanes/filter/roles):**
build on the existing `backend/app/services/agent_bridge/teams.py` model (auto-detect +
manual grouping of already-running sessions) — there is no preset/slot API, and that's
intentional, not an oversight.
