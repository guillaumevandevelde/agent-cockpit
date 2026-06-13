# Scheduled session resume — design

> Extends the existing fase-2 scheduled-messages engine
> (`docs/cockpit/fase-2-spec.md`). Read that first for the base engine.

## Goal

A scheduled message can target **one specific Claude Code session** (picked at
schedule time). When the schedule fires, Cockpit:

- **injects** the message into that session's live tmux pane if it is still
  alive, or
- **relaunches** it with `claude --resume <id>` and then injects the message.

This handles the primary use case: a CC session stopped because it hit a
**usage/session limit**, and we want to resume it — whether the limit-stop left
the interactive pane alive (blocked) or the `claude` process exited.

The trigger is a **manually scheduled time** (the existing once/cron triggers),
not auto-detection of the limit event — auto-detection was judged too
error-prone. The scheduled **message is the payload**; "resume" is a delivery
path, not a separate concept. A message is always present per schedule.

## Scope decisions (confirmed)

| Question | Decision |
|---|---|
| How to know when to resume | **Manual** scheduled time (reuse once/cron). No limit auto-detection. |
| Session state after limit-stop | **Both** can happen: pane alive *or* `claude` exited. Branch on detection. |
| Which conversation to resume | **Pick at schedule time** from a list of recent sessions (id + preview + timestamp + worktree). |
| What to send after resume | **A message written per schedule** (always present). |
| Targeting + alive detection | **Session Registry** fed by hook-reported tmux pane (`$TMUX_PANE`). |
| DB schema change | **Add columns defensively** at startup — do **not** wipe the database. |

## Why session-level (not project-level)

The existing scheduling subsystem is **cwd-keyed**: `discovery` maps tmux panes
by `cwd`, and `idle_state` is keyed by `cwd`. There is **no session-id → pane
mapping**. The user runs **multiple concurrent sessions in one working copy**,
so cwd-level targeting cannot tell those sessions apart. This feature adds
session granularity.

## What already exists (reuse, don't rebuild)

- `providers/claude_code.py::build_spawn_command` already supports
  `mode == "resume"` → `claude --resume <session_id>`, and `resolve_directory`
  already resolves the launch dir from the session's recorded cwd via
  `_resolve_project_directory(project_folder, session_id)`.
- `agent_bridge/resumable.py::list_resumable_sessions(directory, limit, db)`
  already enumerates resumable sessions across a project + its worktrees
  (id, `modified_at`, last-message preview, `worktree_label`). Powers the CC
  Bridge resume picker; reuse it for the schedule-time session picker.
- `agent_bridge/discovery.py::discover_agent_sessions()` returns live panes with
  `pane_id`, `cwd`, `tmux_target`, running-`claude` detection.
- Scheduling engine: `scheduler.py` (APScheduler once/cron) → `crud.py`
  (`run_scheduled_delivery`) → `delivery.py` (`DeliveryEngine.deliver`) →
  `session_resolver.py` + `tmux_inject.py` + `idle_state.py`.
- Hook ingest: `hook_script.py` renders the CC hook command;
  `/api/v1/scheduled-messages/hook-event` feeds `idle_state`.

## Components

### 1. Data model — `models/scheduled_message.py` + `scheduled_message_schemas.py`

New **nullable** columns on `ScheduledMessage`:

- `target_kind`: `"project"` (default — today's behavior) | `"session"`
- `target_session_id`: CC session UUID to resume (when `target_kind="session"`)
- `project_folder`: Claude's encoded project folder (e.g. `-home-guillaume-dev-x`),
  needed so resume launches in the session's recorded cwd
- `session_preview` (optional): last-message snippet captured at create time, for display

`target_project` (cwd) stays populated for display + idle keying.

Schema `Create`/`Response`/`Update` schemas gain the same fields. Validation:
when `target_kind="session"`, `target_session_id` and `project_folder` are
required.

**Defensive column add (no migration framework).** After `create_all` at
startup, run an idempotent step: `PRAGMA table_info(scheduled_messages)`, then
`ALTER TABLE scheduled_messages ADD COLUMN ...` for each new column that is
missing, with a default (`target_kind` defaults to `'project'`). Existing rows
keep working untouched. Lives in a small `ensure_scheduled_message_columns()`
helper invoked from the app lifespan/startup.

### 2. Session Registry + hook enhancement

- **Hook script** (`hook_script.py`): the rendered command also captures
  `$TMUX_PANE` (env var present in the pane's shell) and posts it as
  `tmux_pane` alongside `event`/`session_id`/`cwd`. `HookEvent` schema gains
  optional `tmux_pane: str | None`.
- **`SessionRegistry`** — new in-memory singleton (sibling to `idle_state`), fed
  from the same `/hook-event` endpoint. Maps
  `session_id → {pane_id, cwd, idle: bool, last_seen}`. API:
  - `record(event, session_id, cwd, tmux_pane)` — updates pane map + idle flag
    (idle on `Stop`; busy on `UserPromptSubmit`/`SessionStart`/`Notification`).
  - `pane_for(session_id) -> str | None`
  - `is_idle(session_id) -> bool`
  - `wait_until_idle(session_id, timeout_s) -> bool`
- The existing cwd-keyed `idle_state` stays for the `target_kind="project"`
  path. The hook endpoint feeds both.

### 3. Resolve session → live pane — `session_resolver.py`

New `resolve_session_target(session_id, cwd) -> str | None`:

1. `pane_id = registry.pane_for(session_id)`.
2. If `pane_id` known: confirm via `discover_agent_sessions()` that a live
   `claude` pane with that `pane_id` exists → return its `tmux_target`; else
   `None` (exited).
3. **Cold-registry fallback** (no pane mapping, e.g. after a backend restart):
   inspect discovery for live `claude` panes whose `cwd` matches:
   - **0 panes** → return `None` (resume-spawn).
   - **exactly 1** → return that `tmux_target` (safe to inject).
   - **>1** → raise/return a sentinel meaning *ambiguous* → the attempt fails
     with a clear error; never risk forking a live session by resume-spawning.

New `resume_spawn_for(session_id, project_folder, cwd, permission_mode) -> str`:
calls the **provider-based** spawn (`agent_bridge`/providers, which supports
`mode="resume"`) with `SpawnCommandOptions(mode="resume", session_id,
project_folder, directory=cwd, ...)` and returns the new `tmux_target`.

### 4. Delivery engine — session branch — `delivery.py`

`DeliveryEngine.deliver` gains a session path (new params `target_kind`,
`target_session_id`, `project_folder`; or a dedicated `deliver_session`
method). For `target_kind == "session"`:

1. `target = resolve_session_target(session_id, cwd)`
   - ambiguous (>1 cold pane) → `DeliveryResult(outcome="failed",
     error="ambiguous live sessions, cannot safely resume")`.
2. **Alive** (`target` not None) → `action="used_existing"`; if
   `when_busy="wait_until_idle"`, `registry.wait_until_idle(session_id,
   timeout_s)`; then `send_text(target, message)`.
3. **Exited** (`target` None) → `target = resume_spawn_for(...)`,
   `action="resumed"`. A freshly `--resume`d session loads the conversation and
   waits for input — it does **not** fire a `Stop` hook — so we do **not**
   wait-until-idle here. Instead sleep `resume_settle_s` (default 3s) to let the
   TUI load, then inject.
4. Timeout (default 1800s) and failure handling mirror the existing path.

`DeliveryResult.action` gains a new value: `resumed`.

**Open detail for the plan:** `build_spawn_command` for `mode="resume"`
currently only honors `skip_permissions`, not `--permission-mode acceptEdits`.
Extend it to append `--permission-mode <mode>` from `permission_mode` (matching
the `permission_flags` mapping in `session_resolver.py`).

### 5. Glue — `crud.py`

`run_scheduled_delivery` branches on `msg.target_kind`, passing
`target_session_id` / `project_folder` to the engine for the session path. The
`DeliveryAttempt` row records `action="resumed"` and the resolved tmux target.

### 6. UI — `features/scheduled-messages/`

- **`ScheduledMessageForm.tsx`**: a target-type toggle — *Project message*
  (existing) vs *Resume a specific session*. In resume mode: project picker →
  session dropdown fed by the existing `list_resumable_sessions` endpoint
  (showing last-message preview + `modified_at` + worktree label). Selecting a
  session sets `target_session_id` + `project_folder` + `target_project` (cwd).
  Message field required in both modes. Trigger (timer/cron) + permission mode
  unchanged.
- **`ScheduledMessagesPage.tsx`**: a "resume" badge on session-targeted rows;
  show the session preview.
- **`DeliveryLog.tsx`**: render the new `resumed` action.
- `types.ts` + `api.ts`: add the new fields.

## Delivery flow (session target, once trigger)

1. **Create** (UI) → row saved with `target_kind="session"`,
   `target_session_id`, `project_folder`, `target_project` (cwd) → registered
   with APScheduler (`DateTrigger`).
2. **Fire** → status `pending_delivery`, hand to Delivery Engine.
3. **Resolve** via `resolve_session_target`:
   - alive → wait-until-idle (per-session) → **inject**.
   - exited → `claude --resume <id>` → wait for session idle → **inject**.
   - ambiguous (cold registry, >1 live pane in cwd) → **fail** with clear error.
4. **Send** = `tmux send-keys` (text + Enter) into the target pane.
5. **Finish** → status `delivered`; log the attempt with `action`. (cron →
   back to `scheduled`.)

## Edge cases & error handling

- **Session `.jsonl` deleted** before fire → resume-spawn fails → `failed` + log.
- **Alive but never idle** within timeout → `timeout`; do **not** send.
- **Backend restart** wipes the in-memory registry → cold-registry fallback
  (§3): 0/1/>1 live-pane heuristic; repopulates as hooks fire.
- **Resume of a session already alive elsewhere** → avoided by detection;
  residual risk only in the ">1 cold pane" case, which fails safe.
- **Spawn fails** → `failed` + log (existing behavior).

## Testing (TDD, matching existing pytest style)

- **Unit**
  - `SessionRegistry`: event sequences → pane map + per-session idle transitions.
  - `resolve_session_target`: alive / exited / cold-registry 0-1->1 / ambiguous
    (mocked `discover_agent_sessions` + registry).
  - delivery session-branch: alive→inject, exited→resume-spawn→wait→inject,
    timeout, resume-spawn failure, ambiguous→failed.
  - `hook_script` render includes `tmux_pane`; `HookEvent` accepts it.
  - schema validation: session target requires `target_session_id` +
    `project_folder`.
  - `ensure_scheduled_message_columns()` idempotent ALTER on a pre-existing DB.
- **Integration**: mocked tmux + simulated hook events → both resume paths end
  to end through `run_scheduled_delivery`.
- **Manual WSL e2e**: pick a real session, kill its pane, schedule resume in
  1 min → observe relaunch + inject; plus the alive-pane inject path.

## Non-goals (YAGNI)

- No limit-stop auto-detection / reset-time parsing (manual schedule only).
- No `--continue` / most-recent fallback (explicit session pick only).
- No container isolation, multi-user, or headless model (per fase-2 spec).
