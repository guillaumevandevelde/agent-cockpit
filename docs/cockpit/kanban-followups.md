# Kanban — known follow-ups (post-v1)

These came out of the final code review (2026-06-14). The merge-blockers were fixed
(see commits `d42cde2`, `7769d88`); the items below were consciously deferred.

## Should fix before activating sync / multi-project hardening

- **I4b — `.mcp.json` write path allowlisting.** `enable`/`disable` write `.mcp.json`
  into any directory the backend user can write. The atomic-write corruption risk is
  fixed; the *write-anywhere* surface is not — before exposing Cockpit beyond localhost,
  validate `project_path` against the known/registered projects list.
  - _Update (2026-06-14):_ the auth posture this item assumed is partly addressed.
    An optional bearer token (`api_token`) now guards `/api/v1/*` **and** the
    `/kanban-mcp` mount, and the server binds to `127.0.0.1` (not `0.0.0.0`) by default
    (commit `60c28a3`). `enable` now also embeds the token in the generated `.mcp.json`
    when set. The remaining gap is purely the `project_path` allowlisting above.

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

## Sync milestone (Phase K is only scaffolding today)

- Implement a real `SyncTransport` (Turso/libSQL embedded replica or `sqld`, or a REST
  push/pull) and wire `ops_since` / `ingest_ops` on a schedule.
- Introduce Alembic migrations for the kanban store before a non-wipeable remote primary
  exists (deferred from v1 per the plan; materialized tables remain rebuildable via
  `rematerialize()`).
- Add the optional **push-on-idle** initiative layer (reuse the scheduled-messages delivery
  engine to inject the next card on the `Stop` hook).
