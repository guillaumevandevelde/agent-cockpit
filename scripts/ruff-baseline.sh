#!/usr/bin/env bash
# Capture the list of pre-existing ruff hits on origin/master, so engineer
# sessions can later attribute each hit to "yours" vs "pre-existing" without
# a manual `git stash -u && ruff && git stash pop` dance. Mirrors
# scripts/pytest-baseline.sh exactly — same idempotent cache, same TTL, same
# detached-worktree capture, same FAKE_WORKTREE override for the test
# harness. The only material difference is the parse filter:
#
#   pytest:  grep -E '^(FAILED|ERROR) ' | sed …   (test-name lines)
#   ruff:    grep -E ':[0-9]+:[0-9]+: '           (one line per hit)
#
# Rationale for using `ruff check --output-format=concise`: the default
# output is the rich multi-line diagnostic (arrow markers, code excerpts,
# "Found N errors." footer) — useful for humans, painful to diff. Concise
# gives one stable line per hit (`<file>:<line>:<col>: <CODE> <msg>`)
# which is what set-arithmetic (`comm`) needs to attribute hits across
# runs. Kanban card 070911ee (the original 31-hit cleanup) is the
# precedent; this script exists so the next person who adds a hit doesn't
# have to redo the "which of these is mine?" disambiguation from scratch.
#
# Strategy: check out origin/master in a detached worktree, run
# `ruff check --output-format=concise backend/app backend/tests` there,
# parse the one-line-per-hit shape, write the unique lines to
# `.claude/state/ruff-baseline.txt`. Subsequent runs reuse the cached file
# as long as it's younger than `--max-age-hours` (default 24).
#
# This script does NOT require network access, a running backend, or any
# external service. Pure ruff + git worktree, like the pytest equivalent.
#
# Usage:
#   scripts/ruff-baseline.sh                    # capture (or reuse cache)
#   scripts/ruff-baseline.sh --regen            # force a fresh capture
#   scripts/ruff-baseline.sh --max-age-hours 1  # override cache TTL
#   scripts/ruff-baseline.sh --print            # just print the baseline path + count
#   scripts/ruff-baseline.sh --help
#
# Env overrides:
#   RUFF_BASELINE_PATH             absolute path of the baseline file
#   RUFF_BASELINE_MAX_AGE_HOURS    cache TTL in hours (default 24)
#   RUFF_CMD                       override the ruff binary (test harness)
#   RUFF_CWD                       override the cwd ruff runs in (test harness)
#   RUFF_FAKE_WORKTREE=1           skip the detached worktree dance (test harness)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
STATE_DIR="$REPO_ROOT/.claude/state"
BASELINE="${RUFF_BASELINE_PATH:-$STATE_DIR/ruff-baseline.txt}"
MAX_AGE_HOURS="${RUFF_BASELINE_MAX_AGE_HOURS:-24}"
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
        n=$(wc -l < "$BASELINE")
        echo "$BASELINE ($n pre-existing failures)"
    else
        echo "(no baseline yet — run $0 to capture)"
    fi
    exit 0
fi

# Idempotent: reuse cached baseline when present and fresh.
# This check runs BEFORE env validation — a cached baseline must be usable
# even if the venv has since been deleted or origin/master has been rebased
# out, so read-only callers ("is there a baseline?") always succeed. Same
# shape as scripts/pytest-baseline.sh:81-88.
if [ "$REGEN" = 0 ] && [ -f "$BASELINE" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$BASELINE") ))
    if [ "$age" -lt $((MAX_AGE_HOURS * 3600)) ]; then
        n=$(wc -l < "$BASELINE")
        echo "Using cached baseline: $n pre-existing failures ($((age / 60))m old) → $BASELINE"
        exit 0
    fi
fi

# Validate environment ----------------------------------------------------------------
# Only required when we are actually about to capture a fresh baseline.
if [ ! -d "$BACKEND_DIR" ]; then
    echo "error: $BACKEND_DIR not found — not a worktree checkout?" >&2
    exit 1
fi

# RUFF_CMD (with optional RUFF_CWD) lets the test harness fake ruff for
# isolation tests without touching the real venv. Absent an override, falls
# back through worktree-local venv → shared main-checkout venv → PATH (same
# chain as scripts/pytest-baseline.sh via lib/resolve-pytest-cmd.sh) so this
# works in worktree sessions that have no local venv.
source "$SCRIPT_DIR/lib/resolve-ruff-cmd.sh"
if ! resolve_ruff_cmd "$BACKEND_DIR"; then
    exit 1
fi
RUFF_CWD="${RUFF_CWD:-$BACKEND_DIR}"
if [ ! -x "$RUFF_CMD" ]; then
    echo "error: $RUFF_CMD not executable — run scripts/install.sh first" >&2
    exit 1
fi

if [ -z "${RUFF_FAKE_WORKTREE:-}" ]; then
    if ! git -C "$REPO_ROOT" rev-parse --verify --quiet origin/master >/dev/null; then
        echo "error: origin/master not found locally — 'git fetch origin' first" >&2
        exit 1
    fi
fi

# Capture: ruff on a detached worktree of origin/master ---------------------------
# The detached worktree keeps the engineer's in-flight worktree completely
# untouched — no stash, no checkout dance. RUFF_FAKE_WORKTREE=1 lets the test
# harness inject a synthetic cwd instead of a real worktree. Same shape as
# scripts/pytest-baseline.sh:121-137.
TMP=$(mktemp -d)
# Slot name MUST be unique per session: git derives the `.git/worktrees/<name>`
# entry from the path's basename, so a fixed name collides under concurrent
# runs of this script (or with the pytest-$$ sibling script sharing the same
# repo). `ruff-$$` guarantees a fresh slot per invocation. Same lesson as
# kanban card c23dfe46.
SLOT="ruff-$$"
trap 'git -C "$REPO_ROOT" worktree remove --force --quiet "$TMP/$SLOT" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

if [ -z "${RUFF_FAKE_WORKTREE:-}" ]; then
    if ! git -C "$REPO_ROOT" worktree add --detach "$TMP/$SLOT" origin/master >/dev/null; then
        echo "error: could not create detached worktree of origin/master" >&2
        exit 1
    fi
    RUFF_CWD="$TMP/$SLOT/backend"
fi

mkdir -p "$(dirname "$BASELINE")"

# Run ruff with --output-format=concise so each hit is exactly one line of
# `<file>:<line>:<col>: <CODE> <message>`. The filter `:N:N:` drops the
# footer lines (`Found N errors.`, `[*] N fixable …`) which never match
# that pattern. `set +e` + `|| true` swallows ruff's non-zero exit on hit
# (same rationale as scripts/pytest-baseline.sh:144-151).
set +e
(
    cd "$RUFF_CWD"
    "$RUFF_CMD" check --output-format=concise app tests 2>&1
) | grep -E ':[0-9]+:[0-9]+: ' \
    | sort -u > "$BASELINE"
set -e

n=$(wc -l < "$BASELINE")
echo "Captured baseline: $n pre-existing failures → $BASELINE"
