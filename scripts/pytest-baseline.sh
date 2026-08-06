#!/usr/bin/env bash
# Capture the list of pre-existing pytest failures on origin/master, so
# engineer sessions can later attribute each failing test to "yours" vs
# "pre-existing" without a manual `git stash -u && pytest && git stash pop`
# dance (see kanban card 4c7c5346 for the motivation).
#
# IMPORTANT — scope of the baseline: the failures captured here come from a
# FULL-SUITE pytest run on origin/master. A test that is order-dependent
# (red in a targeted/subset run, green in the full suite) will NOT appear
# in the baseline, even though `pytest-compare.sh` will list it as NEW
# when you reproduce it with e.g. `run-single-test.sh` or
# `pytest tests/test_x.py`. If `pytest-compare.sh` reports a NEW failure
# that surprises you, re-run that test inside the full suite to distinguish
# an ordering artefact from a real regression — a passing full-suite run
# means the failure is order-dependent, not yours. See kanban card
# 446efe9b for the original report.
#
# Strategy: check out origin/master in a detached worktree, run pytest there,
# parse FAILED/ERROR lines, write the unique test names to
# `.claude/state/pytest-baseline.txt`. Subsequent runs reuse the cached file
# as long as it's younger than `--max-age-hours` (default 24) — that is what
# "captured at session start, not regenerated on each pytest invocation"
# means in practice.
#
# This script does NOT require network access, a running backend, or any
# external service. Pure pytest, like the card demands.
#
# Usage:
#   scripts/pytest-baseline.sh                    # capture (or reuse cache)
#   scripts/pytest-baseline.sh --regen            # force a fresh capture
#   scripts/pytest-baseline.sh --max-age-hours 1  # override cache TTL
#   scripts/pytest-baseline.sh --print            # just print the baseline path + count
#   scripts/pytest-baseline.sh --help
#
# Env overrides:
#   PYTEST_BASELINE_PATH        absolute path of the baseline file
#   PYTEST_BASELINE_MAX_AGE_HOURS  cache TTL in hours (default 24)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
STATE_DIR="$REPO_ROOT/.claude/state"
BASELINE="${PYTEST_BASELINE_PATH:-$STATE_DIR/pytest-baseline.txt}"
MAX_AGE_HOURS="${PYTEST_BASELINE_MAX_AGE_HOURS:-24}"
REGEN=0
PRINT_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --regen|--force)        REGEN=1 ;;
        --print)                PRINT_ONLY=1 ;;
        --max-age-hours)        MAX_AGE_HOURS="${2:?}"; shift ;;
        --max-age-hours=*)      MAX_AGE_HOURS="${arg#*=}" ;;
        -h|--help)
            # Print only the leading doc comment block (lines 2..N where the
            # block ends at the first blank line). Section banners later in
            # the file are implementation comments, not user-facing help.
            awk 'NR==1{next} /^$/{exit} {sub(/^# ?/,""); print}' "$0"
            exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# Print-only mode: just report the current state, don't capture anything.
if [ "$PRINT_ONLY" = 1 ]; then
    if [ -f "$BASELINE" ]; then
        # Prefer the count declared in the metadata header (matches what was
        # logged at capture time). Fall back to a non-comment body count for
        # legacy files without a header — which equals `wc -l` since those
        # files have no `#` lines.
        n=$(grep -m1 '^# pytest-baseline: ' "$BASELINE" 2>/dev/null \
            | sed -E 's/^# pytest-baseline:[[:space:]]+([0-9]+).*/\1/' || true)
        if [ -z "$n" ]; then
            n=$(grep -cv '^# ' "$BASELINE" || true)
        fi
        echo "$BASELINE ($n pre-existing failures)"
    else
        echo "(no baseline yet — run $0 to capture)"
    fi
    exit 0
fi

# Idempotent: reuse cached baseline when present and fresh.
# This check runs BEFORE env validation — a cached baseline must be usable
# even if the venv has since been deleted or origin/master has been rebased
# out, so read-only callers ("is there a baseline?") always succeed.
if [ "$REGEN" = 0 ] && [ -f "$BASELINE" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$BASELINE") ))
    if [ "$age" -lt $((MAX_AGE_HOURS * 3600)) ]; then
        # Count non-comment lines so a metadata-headered cache reports the
        # test-name count, not "header + body". For legacy files (no header)
        # this is identical to `wc -l`.
        n=$(grep -cv '^# ' "$BASELINE" || true)
        cached_at=$(grep -m1 '^# captured-at: ' "$BASELINE" 2>/dev/null \
            | sed -E 's/^# captured-at:[[:space:]]+//' || true)
        cached_sha=$(grep -m1 '^# baseline-sha: ' "$BASELINE" 2>/dev/null \
            | sed -E 's/^# baseline-sha:[[:space:]]+//' || true)
        echo "Using cached baseline: $n pre-existing failures ($((age / 60))m old) → $BASELINE"
        [ -n "$cached_at" ]  && echo "  captured-at: $cached_at"
        [ -n "$cached_sha" ] && echo "  baseline-sha: $cached_sha"
        exit 0
    fi
fi

# Validate environment ----------------------------------------------------------------
# Only required when we are actually about to capture a fresh baseline.
if [ ! -d "$BACKEND_DIR" ]; then
    echo "error: $BACKEND_DIR not found — not a worktree checkout?" >&2
    exit 1
fi

# PYTEST_CMD (with optional PYTEST_CWD) lets the test harness fake pytest for
# isolation tests without touching the real venv. Absent an override, falls
# back through worktree-local venv → shared main-checkout venv → PATH (same
# chain as scripts/run-single-test.sh) so this works in worktree sessions
# that have no local venv.
source "$SCRIPT_DIR/lib/resolve-pytest-cmd.sh"
if ! resolve_pytest_cmd "$BACKEND_DIR"; then
    exit 1
fi
PYTEST_CWD="${PYTEST_CWD:-$BACKEND_DIR}"
if [ ! -x "$PYTEST_CMD" ]; then
    echo "error: $PYTEST_CMD not executable — run scripts/install.sh first" >&2
    exit 1
fi

if ! git -C "$REPO_ROOT" rev-parse --verify --quiet origin/master >/dev/null; then
    echo "error: origin/master not found locally — 'git fetch origin' first" >&2
    exit 1
fi

# Capture: pytest on a detached worktree of origin/master -----------------------------
# The detached worktree keeps the engineer's in-flight worktree completely
# untouched — no stash, no checkout dance. PYTEST_CWD is honored so the test
# harness can inject a fake cwd and a fake pytest binary.
TMP=$(mktemp -d)
# Slot name MUST be unique per session: git derives the `.git/worktrees/<name>`
# entry from the path's basename, so a fixed name (e.g. `m`) collides under
# concurrent runs of this script (or with the CLAUDE.md merge recipe sharing
# the same repo) — both target the same gitdir slot, and a stale HEAD leaks
# into the fresh session. `$$` guarantees a fresh slot per invocation. See
# kanban card c23dfe46…
SLOT="pytest-$$"
trap 'git -C "$REPO_ROOT" worktree remove --force --quiet "$TMP/$SLOT" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

if [ -z "${PYTEST_FAKE_WORKTREE:-}" ]; then
    if ! git -C "$REPO_ROOT" worktree add --detach "$TMP/$SLOT" origin/master >/dev/null; then
        echo "error: could not create detached worktree of origin/master" >&2
        exit 1
    fi
    PYTEST_CWD="$TMP/$SLOT/backend"
fi

mkdir -p "$(dirname "$BASELINE")"

# Run pytest inside the (possibly fake) worktree, then filter for FAILED/ERROR
# markers. "set +e" + "|| true" is intentional — pytest exits non-zero when
# tests fail, and that exit code is not an error here, it's the signal we want.
set +e
(
    cd "$PYTEST_CWD"
    "$PYTEST_CMD" --tb=no -q 2>&1
) | grep -E '^(FAILED|ERROR) ' \
    | sed -E 's/^(FAILED|ERROR) +//; s/ +- .*$//' \
    | sort -u > "$BASELINE"
set -e

# Prepend a metadata header so a later pytest-compare.sh run can answer
# "where did this baseline come from?" without forcing the engineer to grep
# two commands (kanban card 53af2e23…: `0 NEW` was ambiguous because the
# baseline provenance was invisible). Schema is a 3-line `# `-prefixed header
# that pytest-compare.sh strips before running `comm` — `# ` (with trailing
# space) is the convention, locked by Task 10 of test_pytest_baseline.sh.
captured_sha="$(git -C "$REPO_ROOT" rev-parse origin/master)"
captured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
n=$(wc -l < "$BASELINE")
tmpfile=$(mktemp)
{
    echo "# pytest-baseline: $n pre-existing failures"
    echo "# captured-at: $captured_at"
    echo "# baseline-sha: $captured_sha"
    cat "$BASELINE"
} > "$tmpfile"
mv "$tmpfile" "$BASELINE"

echo "Captured baseline: $n pre-existing failures → $BASELINE"
echo "  captured-at: $captured_at"
echo "  baseline-sha: $captured_sha"
