---
title: Token-saver integratie achter per-lane opt-in vlag (mechanisch, fail-open)
status: approved
date: 2026-07-24
card: c31333bfdd2c496fb8cb167c5769e9ce
source_decision: ../cockpit/token-saver-mechanismen-decision.md
---

# Token-saver integration behind a per-lane opt-in flag (mechanical, fail-open)

This is the implementation-side spec for kanban card
`c31333bfdd2c496fb8cb167c5769e9ce`. The mechanism (RTK vs. Headroom vs.
Caveman vs. Ponytail) is already chosen — see
[`docs/cockpit/token-saver-mechanismen-decision.md`](../cockpit/token-saver-mechanismen-decision.md).
**RTK** is the selected mechanism, conditional on the per-lane opt-in,
fail-open, board kill-switch, prompt-immutability, and measurement-harness
acceptance gates spelled out there §8 + §10. This spec turns those gates
into concrete files, function signatures, contracts, and tests.

Caveman and Ponytail are out of scope here — they live in their own
follow-up card `d0446fd8…`. This spec stays mechanical-only: nothing in
the dispatched prompt, the persona body, the card text, the plan context,
or the ship instructions is allowed to change. The only effect is
*reducing Bash tool output* by transparently rewriting Bash commands via a
Claude Code `PreToolUse` hook.

## 1. Goals & acceptance criteria

1. **Per-lane opt-in, default off.** A new `token_saver_enabled` boolean
   column on `kanban_columns` (default `false`). A column with the flag
   off behaves exactly as it does today.
2. **Board-wide runtime kill-switch.** No restart, no code change. A new
   `KanbanMeta` key `token_saver:enabled` (default `false`) read on every
   dispatch. Off → helper is a no-op, regardless of the per-lane flag.
3. **Fail-open everywhere.** If RTK is missing, wrong version, the
   settings file is unwriteable, JSON parsing fails, or anything else
   goes wrong, the dispatch continues unchanged. The card's activity
   feed records `**Note:** Token saver not activated: <reason>` and the
   session runs.
4. **Prompt-cache safe.** The dispatch prompt is byte-identical to the
   pre-integration baseline. The only thing that changes is
   `<worktree>/.claude/settings.json`, which lives outside the cached
   message array — Claude Code's prompt cache is keyed on the message
   prefix, not the settings file.
5. **Visible in the activity feed.** Activation posts
   `**Note:** Token saver activated: RTK 0.43.0 (PreToolUse hook)` to
   the card's op-log; fail-open posts
   `**Note:** Token saver not activated: <reason>`. Same prefix shape
   as the other `**Note:** ` audit comments so a future
   card-activity panel can group them.
6. **Measurement acceptance.** Re-running the counterbalanced harness
   with a *real* RTK variant must show non-negative benefit OR
   acceptable quality (golden task `pass_tests=1` on
   the revert task; the former `pass_diff` column was dropped from
   `score_golden` — see `docs/cockpit/prompt-injectors-decision.md`). If the integration shows zero / negative benefit
   or quality regression, the feature is disabled by default and the
   card reports measured outcome to `decisions.md` before shipping.
7. **Tests cover both branches.** Pytest covers the active branch
   (settings file gets the hook, spawn env carries
   `RTK_TELEMETRY=off`, activity comment lands) and the fail-open
   branches (RTK missing, RTK wrong version, settings file
   unwriteable, worktree missing, JSON parse error).

## 2. Architecture

Five small units; each has one job:

| Unit | File | Job |
| --- | --- | --- |
| Per-lane flag | `backend/app/kanban/models.py` (`KanbanColumn.token_saver_enabled`) + `backend/app/kanban/db.py` (`_ensure_column_table`) | Persist the per-lane boolean, additive ALTER TABLE migration |
| Board kill-switch | `backend/app/kanban/dispatch.py` (`is_token_saver_board_enabled`) | Read `KanbanMeta` row `token_saver:enabled` on every dispatch tick |
| Worktree-local hook installer | `backend/app/kanban/token_saver.py` (new) | Discover RTK, verify version, write `.claude/settings.json`, install hook script. Fail-open on every step |
| Dispatch hot-path integration | `backend/app/kanban/dispatch.py` (`_run_card`) | Call installer between worktree creation and `spawn_session`, post audit comment |
| Measurement harness real-RTK variant | `scripts/measure-token-saver.sh` + `scripts/lib/measure_token_saver_lib.sh` | New `real-saver` subcommand; preserve existing proxy as a documented lower bound |
| Frontend opt-in toggle | `frontend/src/features/kanban/components/ColumnSettingsDialog.tsx` + `api.ts` + `types.ts` | Toggle `token_saver_enabled` in the column settings dialog; reuse the existing PATCH path |
| Board kill-switch API | `backend/app/api/v1/kanban/router.py` (`/token-saver`) | Mirror `/autodispatch`, `/skip-permissions`, `/transport` shape |

The router surface for the per-lane toggle is the existing
`PATCH /api/v1/kanban/columns/{column_id}` — the new boolean rides along
on the same payload, same `exclude_unset=True` semantics, same `422`
surface for unknown values.

## 3. Data model

### 3.1 `kanban_columns.token_saver_enabled`

Additive migration in `backend/app/kanban/db.py::_ensure_column_table`:

```python
if "token_saver_enabled" not in cols:
    await conn.exec_driver_sql(
        "ALTER TABLE kanban_columns ADD COLUMN token_saver_enabled "
        "INTEGER NOT NULL DEFAULT 0"
    )
```

Stored as `INTEGER NOT NULL DEFAULT 0` (SQLite has no native boolean;
`0/1` is the codebase convention). Default `0` = feature off everywhere,
which is exactly the "never on by default" acceptance criterion. Existing
rows round-trip as `0` without a backfill.

### 3.2 `KanbanColumn.token_saver_enabled` (ORM)

```python
# Nullable=False so the column never carries an "unset" sentinel;
# default 0 mirrors the SQL DEFAULT. Read access is just a bool coercion.
token_saver_enabled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```

### 3.3 `KanbanMeta` kill-switch

Key: `token_saver:enabled`. Value: `"1"` = on, `"0"` or absent = off.
Same on/off vocabulary as `autodispatch:enabled` so the front-end can
reuse the toggle component. No schema change — the table is a generic
key/value bag.

## 4. Dispatch integration

The integration point is the worktree-creation step in
`backend/app/kanban/dispatch.py::_run_card`, between the existing
`_copy_repo_mcp_json_to_worktree(repo, worktree_path)` call and the
`card_transport` invocation.

```python
from app.kanban import token_saver

# ... existing code through `_copy_repo_mcp_json_to_worktree` ...

token_saver_status = await token_saver.maybe_install(
    session=session,
    card_id=card.id,
    column_name=card.column,
    worktree_path=worktree_path,
    repo_path=repo,
)
# token_saver_status is one of:
#   ("active", "RTK 0.43.0")
#   ("inactive", "<reason>")           # per-lane flag off, kill-switch off, etc.
#   ("failed", "<reason>")             # attempted but couldn't install; dispatch continues
# No exception ever escapes; the helper is responsible for fail-open.
```

The helper **never** mutates the prompt string. It only writes to the
worktree's `.claude/settings.json` (which Claude Code reads at session
start, not at every prompt turn, so the cache key — the message array —
stays stable) and to the spawn environment (`RTK_TELEMETRY=off`).
Concretely the spawn env injection goes through the existing
`SpawnCommandOptions` flow: the helper returns an optional
`extra_env_keys={"RTK_TELEMETRY": "off"}` patch that `_run_card` merges
into `build_spawn_env`.

### Activity-feed comment

After `maybe_install` returns, the dispatch layer posts the audit comment
*via `apply_operation`* so the write goes through the op-log. Shape:

```
**Note:** Token saver activated: RTK 0.43.0 (PreToolUse hook)
```

or

```
**Note:** Token saver not activated: <short reason>
```

Reasons are short, deterministic, and operator-readable. Examples:
`per-lane flag off`, `board kill-switch off`, `rtk binary missing`,
`rtk version 0.42.0 (need >=0.43.0)`, `settings.json not writable`,
`hook script download failed`. Full sentence on a debug-level log line
alongside, so an operator with the card open doesn't need a separate
log dive.

The comment is **deduplicated** against the most recent op on the card
within the same dispatch: if the previous `**Note:** Token saver …`
comment landed within the last 60 seconds, we skip. (Avoids the
"every dispatch cycle re-posts" failure mode during a backlog flush.
The 60s window is generous enough to collapse a same-card re-spawn
without silencing a meaningful state change on a later card.)

### Spawn environment

`RTK_TELEMETRY=off` is the only env var the helper injects. The
existing provider-env layer is untouched — RTK is library-agnostic.

## 5. Worktree-local hook installer (`backend/app/kanban/token_saver.py`)

Single public function:

```python
async def maybe_install(
    session, *, card_id: str, column_name: str,
    worktree_path: str, repo_path: str,
) -> tuple[str, str]:
    """Decide whether to install RTK for this dispatch, and do it.

    Returns (status, reason) where status ∈ {"active", "inactive", "failed"}.
    Never raises; never blocks dispatch.
    """
```

Internal flow (every step is wrapped in a fail-open guard):

1. **Resolve the per-lane flag.** Query `kanban_columns` for the row
   matching `(project_key, name=column_name)`. If `token_saver_enabled`
   is `0` or the column is missing → return `("inactive", "per-lane flag off")`.
2. **Resolve the board kill-switch.** Read `KanbanMeta['token_saver:enabled']`.
   If `0` or absent → return `("inactive", "board kill-switch off")`.
3. **Locate the RTK binary.** Look in this order, return the first hit:
   - `$COCKPIT_RTK_BIN` env var (operator override for ad-hoc testing)
   - `$HOME/.local/share/cockpit/rtk/v0.43.0/rtk` (the bootstrap install)
   - `command -v rtk` (PATH lookup; accepted only if `--version` reports 0.43.0)
   None → return `("failed", "rtk binary missing")`.
4. **Verify version.** `rtk --version` first token after `rtk ` must
   start with `0.43.0`. Anything else → `("failed", f"rtk version {ver} (need 0.43.0)")`.
5. **Materialise the hook script.** Write
   `<worktree>/.claude/hooks/rtk-rewrite.sh` by copying the pinned
   upstream copy from
   `$HOME/.local/share/cockpit/rtk/v0.43.0/hooks/rtk-rewrite.sh`
   (downloaded once at bootstrap, sha256-verified against
   `checksums.txt` in the release). If the worktree copy already
   exists with the same content → no-op. If the source cache is
   missing → download from
   `https://raw.githubusercontent.com/rtk-ai/rtk/v0.43.0/hooks/claude/rtk-rewrite.sh`
   into the cache (sync `urllib`, timeout 5s, no retries — fail-open
   if the network is down). Idempotent and content-addressed.
6. **Materialise the wrapper hook script.** Write
   `<worktree>/.claude/hooks/rtk-cockpit-rewrite.sh`. This is a small
   bash wrapper that:
   - calls the upstream `rtk-rewrite.sh`
   - **bypasses `git diff`** (lossy on shipping lanes; see
     [token-saver-mechanismen-decision.md §4.1.3](../cockpit/token-saver-mechanismen-decision.md))
   - **bypasses bare `grep` without `-r`** (ugrep-shim-val; see §4.1.4)
   - exits 0 on any failure (Claude Code requirement for `PreToolUse`)
7. **Patch `.claude/settings.json`.** Read
   `<worktree>/.claude/settings.json` if present. Parse as JSON. Merge:
   - `hooks.PreToolUse`: append the wrapper entry
     (`{"matcher": "Bash", "hooks": [{"type": "command",
     "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/rtk-cockpit-rewrite.sh"}]}`)
     only if no entry with the same matcher+command already exists.
   - every other key stays untouched. The `permissions.allow`/
     `permissions.deny` lists, `includeCoAuthoredBy`, etc. all
     survive verbatim.
   Write back atomically (`tmp` + `os.replace`, same pattern as
   `_copy_repo_mcp_json_to_worktree` and
   `_write_json_atomic`).
8. **Return `("active", "RTK 0.43.0")`.** The `_run_card` layer picks
   up the audit comment + env injection from here.

Bootstrap (step 5 fallback download + checksum verification) lives in
the same module under `ensure_rtk_cache()`. Called from `init_kanban_db`
on backend startup, **idempotent and best-effort** — if the network is
unavailable, the backend still boots. The first dispatch that actually
wants the saver will fail-open with `("failed", "rtk binary missing")`
and post the audit comment; an operator can then run
`scripts/install-rtk.sh` manually if they want to enable it.

The pinned version `0.43.0` lives in a single constant
`RTK_PINNED_VERSION` at the top of `token_saver.py` so a future bump is
a one-line change.

## 6. Frontend changes

### 6.1 Types

`frontend/src/features/kanban/types.ts`:

```ts
export interface KanbanColumn {
  // ... existing fields ...
  token_saver_enabled: boolean; // SQLite 0/1, surfaced as boolean
}
```

### 6.2 API client

`frontend/src/features/kanban/api.ts`:

- `updateColumn` body adds `token_saver_enabled?: boolean | null`
- New pair `getTokenSaver()` / `setTokenSaver(enabled: boolean)`,
  mirroring `getSkipPermissions` / `setSkipPermissions`. Calls
  `GET /api/v1/kanban/token-saver` and
  `POST /api/v1/kanban/token-saver`.

### 6.3 Column-settings dialog

`ColumnSettingsDialog.tsx`: add a checkbox to the edit row labelled
"Compress Bash output (RTK, opt-in, fail-open)". Sends
`token_saver_enabled: true | false | null`. The board kill-switch
toggle lives in a separate settings surface (project-level) — out of
scope for this card; the kill-switch endpoint exists, the front-end
wires it up in a follow-up card `d0446fd8…` together with the Caveman
toggle.

## 7. Backend API additions

### 7.1 PATCH `/api/v1/kanban/columns/{column_id}` (existing, extended)

`ColumnUpdate` in `backend/app/kanban/schemas.py` adds
`token_saver_enabled: bool | None = None`. Service layer in
`backend/app/kanban/service.py::update_column` already handles
`exclude_unset=True` semantics, so an explicit `null` clears the flag
and an absent field leaves the row untouched. Validation: must be a
literal bool; non-bool values come back as 422 via
`_column_validation_errors`. No provider/model co-validation needed
(this flag never constrains them).

### 7.2 GET/POST `/api/v1/kanban/token-saver` (new)

Mirror the `/autodispatch` shape exactly (single project-scoped bool,
read on every dispatch tick). `KanbanMeta` key
`token_saver:<project_key>` — *project-scoped*, not board-wide: a
follow-up card can introduce a global key without ripping out the
per-project kill-switch. For now, the dispatcher reads the project key
for the card's project. Default absent → off.

```python
@router.get("/token-saver")
async def get_token_saver(project_key: str = Query(...)):
    from app.kanban import token_saver
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "enabled": await token_saver.is_board_enabled(s, project_key)}

@router.post("/token-saver")
async def set_token_saver(payload: TokenSaverRequest):
    from app.kanban import token_saver
    async with KanbanSessionLocal() as s:
        await token_saver.set_board_enabled(s, payload.project_key, payload.enabled)
        await s.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}
```

The kill-switch is read on every dispatch tick — no restart, no code
change. Flipping it off stops new dispatches from installing the hook
on the next tick; already-spawned sessions keep the hook installed
for their lifetime (a Claude Code session doesn't re-read
`settings.json` mid-run). That is the right semantics: an operator
who hits the kill-switch wants to *stop future sessions from gaining
the hook*, not to retroactively strip it from in-flight ones.

## 8. Tests

### 8.1 `backend/tests/test_token_saver.py` (new)

Active branch:

- `test_active_branch_installs_hook_and_env` — worktree flag on,
  kill-switch on, RTK 0.43.0 present → wrapper script written,
  `.claude/settings.json` carries the new matcher, existing
  `permissions.allow` / `PreToolUse` entries survive, activity comment
  posts `activated: RTK 0.43.0`.
- `test_active_branch_idempotent` — two consecutive dispatches on the
  same worktree → wrapper script written once, settings.json not
  double-merged, activity comments both land but the settings diff is
  a no-op the second time.

Fail-open branches:

- `test_fail_open_when_rtk_binary_missing` — `PATH` cleared, no cache,
  no env override → status `failed`, reason contains "rtk binary
  missing", dispatch continues, comment posts
  `not activated: rtk binary missing`.
- `test_fail_open_when_version_wrong` — fake `rtk` binary returns
  `rtk 0.42.0` → status `failed`, reason `rtk version 0.42.0 (need
  0.43.0)`.
- `test_fail_open_when_settings_unwritable` — settings.json is a
  directory, not a file → status `failed`, no exception escapes.
- `test_fail_open_when_worktree_missing` — `worktree_path` doesn't
  exist → status `failed`, no exception escapes.
- `test_fail_open_when_settings_json_corrupt` — settings.json is
  valid text but not valid JSON → status `failed`, original file
  untouched (atomic write).
- `test_inactive_when_per_lane_flag_off` — column has flag off,
  RTK present → status `inactive`, reason `per-lane flag off`, no
  filesystem writes, no activity comment.
- `test_inactive_when_kill_switch_off` — column flag on, kill-switch
  off → status `inactive`, reason `board kill-switch off`.

Settings merge invariants:

- `test_existing_pre_tool_use_hooks_preserved` — settings.json already
  has `hooks.PreToolUse = [{"matcher": "Read", ...}]` → after install,
  both the existing entry AND the new Bash entry are present, in
  that order.
- `test_existing_permissions_preserved` — `permissions.allow`
  contains `["Bash(rm:*)"]` (denied) → survives the merge untouched.
- `test_settings_without_hooks_key` — settings.json is just
  `{"includeCoAuthoredBy": false}` → after install, `hooks` key
  appears with the new entry, `includeCoAuthoredBy` survives.

Deduplication:

- `test_dedup_skips_repeat_within_60s` — two `maybe_install` calls
  within 60s on the same card → second call's activity comment is
  suppressed.
- `test_dedup_does_not_skip_after_60s` — two calls >60s apart on the
  same card → both comments land.

### 8.2 `backend/tests/test_dispatch_token_saver_integration.py` (new)

- `test_dispatch_passes_rtk_telemetry_off_in_spawn_env` — patch the
  spawn function (via `monkeypatch.setattr` on the `dispatch` module's
  binding), assert `extra_env["RTK_TELEMETRY"] == "off"` when the
  helper returned `active`.
- `test_dispatch_omits_rtk_telemetry_off_when_inactive` — helper
  returned `inactive` → `RTK_TELEMETRY` not in `extra_env`.
- `test_dispatch_continues_after_helper_returns_failed` — helper
  returned `failed` → `spawn_session` is still called with the same
  arguments minus the env patch; no `Impediment` move, no
  `dispatch_failures` increment.

### 8.3 Frontend tests

`frontend/src/features/kanban/components/ColumnSettingsDialog.test.tsx` —
new test: toggling the checkbox sends `token_saver_enabled: true` on
Save; the dialog re-renders with the new state from the PATCH
response. (Skipped if the project has no existing
`ColumnSettingsDialog.test.tsx` — created from scratch with one happy
test + one negative "unchanged field is omitted" test.)

### 8.4 Measurement harness

Extend `scripts/measure-token-saver.sh` with a new `real-saver`
subcommand:

- Resolves RTK via the same helper path the dispatcher uses
  (`$COCKPIT_RTK_BIN` → cache → PATH).
- Writes the wrapper hook into the scratch worktree's
  `.claude/settings.json`.
- Runs the same `claude -p ...` invocation as `with-saver`, just with
  the hook instead of the prompt proxy.
- Score with the same `score_golden` (pass_tests).
- The `compare` subcommand extends the table with a
  `real-saver` row per trial. The existing `with-saver` row stays as
  the documented proxy lower-bound.

A small `scripts/lib/measure_token_saver_lib.sh::apply_real_saver`
helper owns the per-worktree install. It reuses the *exact same*
JSON-merge code path the dispatch helper uses (so the measurement
covers the production code path), extracted to a sync helper inside
`token_saver.py` so both call sites stay in lockstep.

## 9. Decisions.md & follow-ups

New row in `docs/cockpit/decisions.md` dated 2026-07-24:

```
| 2026-07-24 | c31333bf… | Token-saver (RTK 0.43.0) integrated per-lane opt-in, fail-open, board kill-switch | — | this row |
```

Plus a measured-outcome line right below it, recorded **after** running
the harness per §10 below. If the measured benefit is zero/negative or
quality regresses, the row reports the disable decision and the
`default off` acceptance criterion stands: opt-in still works for
operators who want to test it on their own lanes, the feature ships
behind the flag, and a follow-up card investigates why real-RTK
underperformed the proxy.

Update
[`docs/cockpit/token-saver-mechanismen-decision.md`](../cockpit/token-saver-mechanismen-decision.md)
with a `✅ Geïmplementeerd (kaart c31333bf…)` line at the top of §8.

Caveman and Ponytail: out of scope. Follow-up card `d0446fd8…` covers
them; this spec adds a TODO comment in `token_saver.py` near the
`maybe_install` signature pointing at that card so a future engineer
extending the helper has the explicit pointer.

## 10. Acceptance run

After implementation, **before** moving the card to Done:

1. `bash scripts/run-single-test.sh backend/tests/test_token_saver.py` —
   full unit coverage green.
2. `bash scripts/run-single-test.sh backend/tests/test_dispatch_token_saver_integration.py` —
   full integration coverage green.
3. Run `scripts/measure-token-saver.sh real-saver` once. Capture the
   `**Note:** Token saver activated: RTK 0.43.0` op on the synthetic
   measurement card and the resulting `usage.json`.
4. Run `scripts/measure-token-saver.sh compare` once. Capture the full
   table (baseline / with-saver / real-saver / deltas).
5. Decide:
   - `real-saver` shows `pass_tests=1` AND non-negative
     token delta vs `baseline` → GO. Card moves to Done with measured
     numbers in the summary.
   - Otherwise → the feature ships disabled-by-default (which is the
     spec's stance anyway) and the summary reports the measured outcome
     honestly: "integration in place, harness showed N% / -N%, feature
     remains opt-in only". Operator can still toggle per lane for
     qualitative testing; card closure is not blocked by the
     measurement.

## 11. Risks & explicit non-goals

- **Lossy diff on shipping lanes** — addressed by the wrapper bypass in
  §5 step 6. If a future lane wants the lossy mode for non-decision
  reads (e.g. `git diff` during a read-only summary), the wrapper
  exposes a per-lane allow-list via the same `token_saver_enabled`
  boolean plus a future `token_saver_lossy_diff: bool` column; that
  is a follow-up card.
- **Network at bootstrap** — `ensure_rtk_cache()` falls back to
  fail-open. Operators who need RTK in air-gapped environments run
  `scripts/install-rtk.sh` to seed the cache from a tarball. (Listed
  as a follow-up; the air-gap path is not required for this card to
  ship.)
- **Prompt-cache safety regression** — guarded by §1 #4 (helper writes
  only settings.json + env). A targeted test asserts the `prompt`
  parameter to `spawn_session` is byte-identical between active and
  inactive branches.
- **Multi-device sync** — `token_saver:enabled` is per-project and
  routed through the op-log like every other `KanbanMeta` flag, so a
  second device sees the toggle the next tick. No new sync story.
- **Multi-account concurrency on a shared box** — `~/.local/share/cockpit/rtk/`
  is per-`$HOME`, which on this WSL box is per-user. Other Linux
  users on the same machine have their own cache; this card does not
  change that.
- **Caveman and Ponytail** — explicitly out of scope (card
  `d0446fd8…`).

## 12. Files touched

New:

- `backend/app/kanban/token_saver.py`
- `backend/tests/test_token_saver.py`
- `backend/tests/test_dispatch_token_saver_integration.py`
- `docs/superpowers/specs/2026-07-24-token-saver-integration-design.md` (this file)

Modified:

- `backend/app/kanban/models.py` — `KanbanColumn.token_saver_enabled`
- `backend/app/kanban/db.py` — additive ALTER TABLE in `_ensure_column_table`
- `backend/app/kanban/schemas.py` — `ColumnUpdate.token_saver_enabled`,
  `TokenSaverRequest`
- `backend/app/kanban/service.py` — `update_column` already handles
  new kwargs (no change); add `is_column_token_saver_enabled(session,
  project_key, column_name)` helper for the dispatch hot path
- `backend/app/kanban/dispatch.py` — call `token_saver.maybe_install`
  in `_run_card`, post activity comment, inject `RTK_TELEMETRY=off`
  into spawn env when status == `active`
- `backend/app/api/v1/kanban/router.py` — `GET/POST /api/v1/kanban/token-saver`,
  extended `PATCH /columns/{column_id}`
- `frontend/src/features/kanban/types.ts` — `KanbanColumn.token_saver_enabled`
- `frontend/src/features/kanban/api.ts` — `getTokenSaver` /
  `setTokenSaver`, `updateColumn` body extended
- `frontend/src/features/kanban/components/ColumnSettingsDialog.tsx` —
  RTK checkbox in the edit row
- `scripts/measure-token-saver.sh` — new `real-saver` subcommand
- `scripts/lib/measure_token_saver_lib.sh` — new
  `apply_real_saver <worktree>` helper
- `docs/cockpit/decisions.md` — new row
- `docs/cockpit/token-saver-mechanismen-decision.md` — `✅ Geïmplementeerd`
  marker at top of §8
