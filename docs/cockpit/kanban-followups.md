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

**Update (2026-07-08) — that card was picked up and the cluster was split, not built
as-is.** `ebecf1c`/`ba50de8`/`1840c05`/`ded4f35`/`6577a87` (team lanes, team filter, team
roles, slot colors, the fullscreen design spec) all read `session.team_preset_id` /
`team_slot_name` / `team_slot_role`, which upstream's `discovery.py` only populates from
`CLAUDE_DECK_TEAM_PRESET_*` tmux env vars set by the rejected preset launch-orchestration
— our sessions never get those vars, so the fields are always empty and the UI would be
dead weight. `465d354`/`ae5562e` (terminal contrast/light-theme fixes) exclusively patch
`TeamLanesView.tsx` and `frontend/src/lib/agentTeamColors.ts`, neither of which exist here.
`2b77891`/`0352e21` (keyboard shortcuts + their discoverability overlay) implement a
leader-key (Ctrl+Space) scheme whose only real actions are "prev/next/jump to *displayed
pane*" — i.e. navigating between `TeamLanesView` panes; without that view there's nothing
to navigate between. None of these nine commits were ported.

What *was* independent and got cherry-picked onto `master`: `e6756a7`/`efe0755` (Agent
Bridge image attachments — paste/drag-drop an image into a tmux session, with the paste
endpoint enforcing an attached interactive relay). Adapted to drop upstream's MCP shim
integration (`agent_mail_server.py` doesn't exist in this fork) and to fit our
already-diverged `TerminalView.tsx` (no team-slot theming, no `instance`/`session` props).

## Upstream removed legacy Presence — deliberately NOT adopted (2026-07-08)

Decision: **keep Presence as-is.** See the trade-off in
`upstream-presence-removal-decision.md`. Upstream's `588cf6c`/`b4e3e87` disable-then-remove
Presence on `upstream/master`, but that history diverged from ours after the fork point
(`42429f3`) — it's not a cleanup we missed, it's a consequence of upstream's own,
independent direction. Our fork kept building on Presence after the fork (attention
notifications, `tmux_pane` plumbing, cascade-delete, flaky-test fixes, most recently
2026-07-02), and CC Bridge's attention indicator
(`frontend/src/features/cc-bridge/useAttentionByPane.ts`,
`frontend/src/hooks/useAttentionNotifications.ts`) reads live off the Presence websocket —
removing it would break that feature with no replacement. No code or `CLAUDE.md` change
needed; `CLAUDE.md` already correctly lists Presence as a current feature/route.
