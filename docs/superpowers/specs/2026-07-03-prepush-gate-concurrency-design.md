# Reliable pre-push gate under concurrent multi-agent load

**Date:** 2026-07-03
**Component:** `.githooks/pre-push` (the branch safety gate)
**Status:** approved design

## Problem

`.githooks/pre-push` runs the full backend pytest suite plus a frontend
ESLint + Vite build on every push, for every checkout and worktree
(`core.hooksPath=.githooks`). The gate logic itself is correct, but there is
**no coordination between concurrent runs**. When 4+ engineer agents dispatched
onto kanban cards push around the same time on one WSL box, the box runs
4× full pytest suites + 4× Vite builds at once. The result is CPU/IO starvation
severe enough that the gate appears to hang — the failure a session reported
after having to ship with `--no-verify`.

Contributing factors found during investigation:

1. **CPU/IO thundering herd (primary).** N unbounded full suites + builds in
   parallel. Nothing is broken; it just crawls to a standstill.
2. **Main-checkout DB fight (secondary).** Each *worktree* already gets its own
   `backend/claude_registry.db` (relative sqlite URL resolves against the
   worktree CWD), so cross-worktree DB contention is not real. But a gate run
   from the **main checkout** uses the production `claude_registry.db` — the same
   file the live `cockpit.sh` dev backend holds open — which is a genuine SQLite
   writer-lock stall, and it also pollutes production data with test rows.
3. **No timeout anywhere.** No `pytest-timeout`, no wrapper `timeout`. A
   genuinely stuck run hangs forever instead of failing loud.

## Goals / non-goals

**Goals:** make the gate reliable under concurrent load without weakening what it
checks. **Non-goals:** change *what* the gate runs (still full pytest + full
build), touch the tree-wipe guard or auto-rebase, or rework the app's own DB
config.

## Design

### 1. Serialize the heavy checks with a shared `flock`

- Lock file at the **absolute** git *common* dir:
  `"$(cd "$(git rev-parse --git-common-dir)" && pwd)"/cockpit-prepush.lock`.
  The common dir is shared by the main checkout and every linked worktree, so
  all gate runs contend on one lock file.
- Only the **test + build** section is inside the lock. The tree-wipe guard
  (reads stdin, must always run first) and the auto-rebase (may hit the network)
  stay outside.
- Acquire: try `flock -n` first; if busy, print a "waiting" line, then
  `flock -w $LOCK_WAIT` (blocking, default **900s**). If the wait times out:
  **warn and proceed without the lock** rather than block a push forever
  (deliberate last resort — matches the reported preference to wait then run).
- If the `flock` binary is absent: warn loudly and run unserialized — same
  "degrade loudly rather than block a push we can't coordinate" philosophy the
  hook already uses for a missing venv / node_modules.

### 2. Per-run `timeout` safety net

- Wrap each heavy invocation in `timeout -k 30 $RUN_TIMEOUT` (default **600s**):
  the `pytest` run and the `lint && build` run each get their own budget.
- Exit code **124** (and **137**, TERM-ignored → KILL) is classified as a
  **timeout failure** (`status=1`) with a clear "timed out — likely resource
  starvation" message. Any other non-zero is a normal check failure.
- Because the timeout fires *inside* the lock, a stuck run dies and **releases
  the lock** instead of poisoning the queue for every waiter.
- If `timeout` is absent: run without it (degrade loudly).

### 3. flock fd must not leak into child processes

**Verified in this environment (bash 5.3.9):** an `{fd}`-opened flock descriptor
is **inherited by child processes** (not close-on-exec). Without mitigation, a
pytest/npm grandchild that outlives a `timeout` kill would keep the lock fd open
and hold the lock forever.

**Mitigation:** the parent shell opens the lock fd (fixed high number `200`) and
keeps it open for the whole critical section, but every heavy child command is
run with its own `200>&-` redirection so the child (and its descendants) never
inherit the lock fd. The parent alone owns the lock; orphaned children cannot
leak it. Lock is released by closing `200` before the auto-rebase.

Residual: this is the standard, bounded shell-flock behavior. Even in a
pathological leak the system self-heals — waiters time out after `LOCK_WAIT` and
proceed unserialized.

### 4. Isolated throwaway DB for the gate's pytest

- Before pytest: `GATE_TMP="$(mktemp -d)"`, `TMPDB="$GATE_TMP/gate.db"`.
- Run pytest with an inline, command-scoped
  `DATABASE_URL="sqlite+aiosqlite:///$TMPDB"` (four-slash absolute URL — verified
  shape). `pydantic-settings` (`case_sensitive=False`, no prefix) maps the env
  var onto `Settings.database_url`, and the override is read at process start, so
  it takes effect cleanly.
- `rm -rf "$GATE_TMP"` on exit (covers `.db`/`-wal`/`-shm`) via an `EXIT` trap.
- Removes writer contention with the live backend **and** stops the suite
  polluting the production DB.

### 5. Tunable knobs (env overrides)

- `COCKPIT_GATE_LOCK_WAIT` — flock blocking wait, default `900`.
- `COCKPIT_GATE_RUN_TIMEOUT` — per-run timeout, default `600`.

## Structure & testability

The hook is rewritten as **sourceable functions** with the actual run guarded by
`[ "${BASH_SOURCE[0]}" = "$0" ]`, so a test can `source` the hook and exercise
its functions without triggering a real push. Kept self-contained (no external
lib) to avoid cross-file version skew for such a critical file.

Functions: `gate_say/warn/fail`, `gate_lock_path`, `gate_acquire_lock`,
`gate_release_lock`, `gate_timed` (timeout + exit-code classification),
`gate_run_backend`, `gate_run_frontend`, `gate_tree_wipe_guard`,
`gate_autorebase`, `main`.

## Testing

`scripts/test_prepush_gate.sh` (bash harness, in the style of
`scripts/test_cockpit.sh`) sources the hook and asserts:

1. `gate_timed` returns success for a fast command, classifies a `sleep`
   over-budget as a **124 timeout failure** with the timeout message, and
   preserves an ordinary non-zero exit.
2. **Serialization:** two concurrent acquisitions of the same lock file do not
   overlap (marker-file / timestamp check).
3. **Lock-wait-then-proceed:** with the lock held externally and a tiny
   `LOCK_WAIT`, acquisition warns and still proceeds unserialized.
4. **fd non-inheritance (regression guard):** a child spawned inside the locked
   critical section does **not** have the lock fd open.
5. **DB isolation:** the pytest invocation sees `DATABASE_URL` pointing at the
   temp file, and a gate run leaves `backend/claude_registry.db` untouched.

Real end-to-end verification (beyond the harness):

- Run the backend suite with `DATABASE_URL` pointed at a fresh temp DB to prove
  the suite is hermetic on an empty database and does not touch the prod DB.
- Perform an actual push to exercise the full modified gate.

## Rollout

Self-contained change to `.githooks/pre-push` + new `scripts/test_prepush_gate.sh`.
The on-disk hook is the tracked file, so every worktree/clone picks it up at its
checked-out commit. Emergency bypass remains `git push --no-verify`.
