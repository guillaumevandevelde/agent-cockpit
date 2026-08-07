#!/usr/bin/env bash
# Run a single pytest test file or single test function in <5s without
# dragging in the full pytest-baseline.sh dance. This is the documented
# exception to `feedback_no_local_pytest` — the rule forbids the full suite
# (multi-minute stalls under contention from the shared box), but a single
# test run is <1s on this box and copy-pasteable for any "I added a new test,
# let me check it passes" workflow. See kanban card
# ed09173c14c248e0a7d4d413f7f2d945 for the missing-recipe gap that
# motivated this script.
#
# Usage:
#   scripts/run-single-test.sh tests/test_x.py                    # whole file
#   scripts/run-single-test.sh tests/test_x.py::test_y            # one test
#   scripts/run-single-test.sh tests/test_x.py::test_y[a-b]       # parametrized
#   scripts/run-single-test.sh tests/test_x.py -k "param_id"      # pytest -k
#   scripts/run-single-test.sh -h | --help
#
# Behavior:
# - Runs pytest with cwd = backend/ so `tests/...` paths resolve naturally.
# - Forces a hard --timeout=10 cap (override with RUN_SINGLE_TEST_TIMEOUT=<s>).
#   The cap exists to keep this from turning into a "run the whole suite"
#   if a missing-glob expands too wide. Single-file/-test runs are <1s on
#   this box; <5s gate is the acceptance criterion in the card.
# - Uses pytest-timeout's ``--timeout-method=thread`` (NOT ``signal``) so
#   the 10s cap actually fires on asyncio-blocking I/O patterns — see the
#   Run section below for why ``signal`` is unreliable here (kaart 103718db).
# - Default verbosity is -q with --tb=short — a glance is enough to see
#   what passed / what broke.
# - Exit code is pytest's exit code (0 = green, 1 = tests-failed,
#   2 = interrupted, 5 = no tests ran). Caller can chain on `$?` directly.
#
# Venv resolution (first hit wins):
#   1. $PYTEST_CMD env override — lets the test harness inject a fake pytest
#      binary so this script can be exercised without touching the real venv.
#   2. <repo>/backend/venv/bin/pytest — the worktree-local venv. Rarely
#      present because .gitignore drops venv/ and scripts/install.sh is not
#      run in worktrees, but if the user did create one we honour it first
#      so the worktree's own deps (if they differ from master) win.
#   3. /home/vdvgu/claude-cockpit/backend/venv/bin/pytest — the shared
#      main-checkout venv. This is the canonical fixture for "engineer
#      in a worktree, want to run one test"; the cwd-trap note in CLAUDE.md
#      explains why we run pytest from here instead of bootstrapping a venv
#      per worktree.
#   4. bare `pytest` on PATH — works if a venv is already activated for
#      this shell.
#
# This script does NOT:
# - Touch git state (no stash, no checkout, no worktree manipulation).
# - Run the full suite, even by accident — the --timeout=10 is the guard.
# - Require an active backend, database, or any external service. It shells
#   out to pytest the same way pytest-baseline.sh does, with the same
#   PYTEST_CMD/PYTEST_CWD env-var hooks for the test harness.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
TIMEOUT_SECONDS="${RUN_SINGLE_TEST_TIMEOUT:-10}"

usage() {
    awk 'NR==1{next} /^$/{exit} {sub(/^# ?/,""); print}' "$0"
}

# Arg parsing ---------------------------------------------------------------
if [ $# -eq 0 ]; then
    usage >&2
    echo "error: missing TEST_TARGET (e.g. tests/test_x.py::test_y)" >&2
    exit 2
fi
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

TEST_TARGET="$1"
shift
EXTRA_ARGS=("$@")

# Eager reject for frontend test paths. `run-single-test.sh` is a pytest
# wrapper — a `.ts`/`.tsx` argument would otherwise hit the file-existence
# check and print a misleading "test file not found" against pytest,
# leaving the author to reverse-engineer the right invocation. The
# matching vitest recipe lives in the CLAUDE.md `# Test`-blok.
#
# Guard: only the FILE path's extension counts. `tests/test_x.py::[a-z]`
# still works (pytest's `::func` / `[params]` syntax is untouched). We
# also accept the bare stem (`.ts`) so the check fires on
# `frontend/src/foo.ts` as well, not just `.tsx`.
case "$TEST_TARGET" in
    *.ts|*.tsx)
        echo "error: '$TEST_TARGET' looks like a frontend test path; run-single-test.sh is a pytest wrapper." >&2
        echo "  Use vitest directly instead (the local binary, not \`npx\`):" >&2
        echo "    ( cd frontend && ./node_modules/.bin/vitest run $TEST_TARGET )" >&2
        exit 2
        ;;
esac

# Venv resolution -----------------------------------------------------------
if [ -n "${PYTEST_CMD:-}" ]; then
    :   # PYTEST_CMD override (used by scripts/test_run_single_test.sh)
elif [ -x "$BACKEND_DIR/venv/bin/pytest" ]; then
    PYTEST_CMD="$BACKEND_DIR/venv/bin/pytest"
elif [ -x /home/vdvgu/claude-cockpit/backend/venv/bin/pytest ]; then
    PYTEST_CMD=/home/vdvgu/claude-cockpit/backend/venv/bin/pytest
elif command -v pytest >/dev/null 2>&1; then
    PYTEST_CMD="$(command -v pytest)"
else
    echo "error: pytest not found. Tried:" >&2
    echo "  - \$PYTEST_CMD (unset)" >&2
    echo "  - $BACKEND_DIR/venv/bin/pytest (worktree-local venv; .gitignored — run scripts/install.sh to create one)" >&2
    echo "  - /home/vdvgu/claude-cockpit/backend/venv/bin/pytest (shared main checkout venv)" >&2
    echo "  - bare 'pytest' on PATH (activate a venv first)" >&2
    exit 1
fi

if [ ! -x "$PYTEST_CMD" ]; then
    echo "error: PYTEST_CMD=$PYTEST_CMD is not executable" >&2
    exit 1
fi

# Sanity: target must point at a file that exists. Strip the optional
# `::func` (and any parametrize suffix `[...]`) so the check operates on
# the file path alone — otherwise `tests/test_x.py::test_y` looks like a
# literal path with a `::` separator in it, which can never exist.
FILE_PATH="${TEST_TARGET%%::*}"   # everything before the first "::"
if [ ! -e "$BACKEND_DIR/$FILE_PATH" ] && [ ! -e "$REPO_ROOT/$FILE_PATH" ]; then
    echo "error: test file not found: $FILE_PATH" >&2
    echo "  looked under: $BACKEND_DIR/ and $REPO_ROOT/" >&2
    exit 1
fi

# Run -----------------------------------------------------------------------
# `set +e` is intentional — pytest exits non-zero when tests fail (1),
# when no tests are collected (5), or when a timeout fires. Caller cares
# about the exact code, not "the script crashed".
#
# `--timeout-method=thread` is the deliberate choice over `signal`:
# pytest-timeout's `signal` method sends SIGALRM to the main thread, but
# the asyncio event loop only delivers that signal at the next yield
# point — and a tight blocking sync I/O call inside an `async def`
# (e.g. `Path.iterdir()` over a 956-file / 523 MB tree in
# `UsageService.discover_jsonl_files`) starves the loop indefinitely, so
# the documented 10s safety net silently never fires (kaart 103718db...).
# The `thread` method uses a watchdog thread that hard-kills the
# process, which interrupts *any* blocking pattern — the timeout
# guarantee the script promises actually holds.
set +e
(
    cd "$BACKEND_DIR"
    "$PYTEST_CMD" \
        --timeout="$TIMEOUT_SECONDS" \
        --timeout-method=thread \
        -q \
        --tb=short \
        "$TEST_TARGET" \
        "${EXTRA_ARGS[@]}"
)
rc=$?
set -e

if [ "$rc" -eq 5 ]; then
    echo "hint: pytest collected 0 tests. Check the target — common causes:" >&2
    echo "  - typo in the function name after ::" >&2
    echo "  - -k filter that excludes everything" >&2
    echo "  - test module was skipped by an env-var (e.g. \"no DB\")" >&2
fi
exit "$rc"
