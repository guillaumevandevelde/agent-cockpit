# Agent Bridge — Session Rename

## Goal

Let the user name an Agent Bridge session meaningfully instead of the auto-generated
`<basename>-<uuid4>`. Two entry points:

1. **At spawn** — the session name defaults to the *feature name* (the worktree name when
   spawning in worktree mode), and is overridable via an optional "Session name" field.
2. **After spawn** — rename an existing session inline from its card.

## Decisions

- **Rename the real tmux session** (`tmux rename-session`), not a separate display-name
  overlay. Single source of truth, no persistent storage (the repo has no migration
  system), survives backend restart, and discovery already reads the tmux session name.
- **Attention is unaffected.** Badges join on `pane_id` (`%N`), which is stable across a
  rename (`useAttentionByPane.ts:43`). Only `tmux_target` (`<session_name>:0.0`) changes;
  the UI refreshes and re-selects the pane by `pane_id` after a rename.
- **No DB, no display-name overlay, no rename history.** Out of scope.

## Naming rules

A single helper sanitizes and de-duplicates a candidate name:

- Sanitize to `[a-zA-Z0-9_-]` (replace other chars with `-`), trim to 20 chars, matching
  the existing `_session_name_for` behaviour.
- Reject empty results after sanitizing.
- Ensure uniqueness against currently running tmux sessions; on collision append
  `-<uuid4 hex[:4]>`. (Auto-generated names keep their uuid suffix unconditionally, as
  today, so spawn never collides.)

## Backend

`backend/app/services/agent_bridge/spawn.py`

- `_sanitize_session_name(raw: str) -> str` — sanitize + trim (extracted from
  `_session_name_for`).
- `_session_name_for(directory, preferred=None)` — if `preferred` is given, sanitize it and
  ensure uniqueness against running tmux sessions (append `-<hex4>` on collision); else the
  current `<basename>-<uuid>` behaviour.
- `spawn_session(provider_id, options, session_name=None)`:
  - Resolve the preferred name: explicit `session_name` → else `options.worktree_name`
    (worktree mode) → else `None`.
  - `name = _session_name_for(directory, preferred=...)`.
  - Existing worktree-name defaulting still applies when `worktree_name` is empty.
- `rename_session(old_name: str, new_name: str) -> dict`:
  - Sanitize `new_name`; reject empty (`ValueError`).
  - If sanitized == `old_name`, no-op success.
  - Collision-check against running tmux sessions → `ValueError` if taken.
  - `tmux rename-session -t <old> <new>` (handle non-zero / FileNotFound / timeout like the
    other helpers).
  - Move the `_spawned_sessions[old_name]` entry to the new key so worktree cleanup on kill
    still resolves.
  - Return `{ "renamed": True, "session_name": new, "tmux_target": f"{new}:0.0" }`.
- Helper to list current tmux session names (`tmux list-sessions -F '#{session_name}'`) for
  collision checks; empty list on any tmux error.

`backend/app/api/v1/agent_bridge/router.py`

- `SpawnRequest`: add `session_name: str | None = None`; pass it into `spawn_session`.
- New endpoint:
  ```
  POST /agent-bridge/sessions/{target}/rename
  body: { "name": str }
  ```
  `target` here is the tmux session name (the part before `:`). Call
  `rename_session(target, body.name)`. `ValueError` → HTTP 400.

## Frontend

`frontend/src/features/cc-bridge/types.ts`

- `SpawnSessionRequest`: add `session_name?: string`.
- Add `RenameSessionResponse { renamed: boolean; session_name: string; tmux_target: string }`.

`frontend/src/features/cc-bridge/api.ts`

- `renameSession(sessionName: string, name: string): Promise<RenameSessionResponse>` →
  `POST agent-bridge/sessions/{sessionName}/rename`.

`frontend/src/features/cc-bridge/NewSessionDialog.tsx`

- Add a `sessionName` state + optional **"Session name"** `Input` (all modes, placed near
  the top with Provider/Mode). Helper text: "Optional. Defaults to the worktree name, or an
  auto-generated name."
- Include `...(sessionName.trim() && { session_name: sessionName.trim() })` in the spawn
  request. Reset on close like the other fields.

`frontend/src/features/cc-bridge/SessionCard.tsx`

- Add a pencil (`Pencil` from `lucide-react`) button next to the kill button.
- Clicking it switches the name span to an inline `Input` seeded with `session_name`.
- Enter / blur → `onRename(session, value)`; Escape → cancel. Use `e.stopPropagation()` so
  it doesn't trigger card selection; the input must not propagate Enter/Space to the card.

`frontend/src/features/cc-bridge/SessionList.tsx` / `CCBridgePage.tsx`

- Thread an `onRename` handler from the page down to each card.
- Handler: call `renameSession(session.session_name, newName)`, then `refresh()` the session
  list, and if the renamed session was the selected/active pane, re-select it by `pane_id`
  (its `tmux_target` has changed).

## Error handling

- Backend rejects empty-after-sanitize and collisions with HTTP 400; the dialog already
  surfaces spawn errors, and the card shows the rename error inline (revert to the old name).
- tmux failures bubble up as `ValueError` → 400 with the tmux stderr, consistent with
  `kill_session` / `spawn_session`.

## Testing

- Backend unit tests (`backend/tests/`) for `_sanitize_session_name`, `_session_name_for`
  with `preferred` + collision, and `rename_session` (success, empty name, collision),
  mocking `subprocess.run` / the tmux session list.
- Manual: spawn a worktree session → card shows the worktree name; rename via pencil →
  list refreshes, attention badge still tracks the pane, kill + worktree cleanup still works.
- (Frontend tests not yet set up in this repo — manual verification for the UI.)
