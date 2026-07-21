#!/usr/bin/env bash
# Capture the list of pre-existing bash-test failures on origin/master, so
# engineer sessions can later attribute each failing `scripts/test_*.sh`
# harness to "yours" vs "pre-existing" without a manual
# `git stash -u && bash && git stash pop` dance (see kanban card
# ecea763e802a4cd59011652dd2537839 for the motivation).
#
# Strategy: check out origin/master in a detached worktree, run every
# `scripts/test_*.sh` there, parse the `  FAIL: <description>` lines each
# harness emits, write a sorted+unique list to
# `.claude/state/bash-test-baseline.txt`. Subsequent runs reuse the cached
# file as long as it's younger than `--max-age-hours` (default 24) — that
# is what "captured at session start, not regenerated on each invocation"
# means in practice.
#
# Companion to scripts/compare-bash-tests.sh, which diffs the captured
# baseline against the current worktree.
#
# This script does NOT require network access, a running backend, or any
# external service. Pure bash + git worktree, like the pytest equivalent.
#
# Usage:
#   scripts/baseline-bash-tests.sh                    # capture (or reuse cache)
#   scripts/baseline-bash-tests.sh --regen            # force a fresh capture
#   scripts/baseline-bash-tests.sh --max-age-hours 1  # override cache TTL
#   scripts/baseline-bash-tests.sh --print            # just print the baseline path + count
#   scripts/baseline-bash-tests.sh --help
#
# Env overrides:
#   BASH_TEST_BASELINE_PATH        absolute path of the baseline file
#   BASH_TEST_BASELINE_MAX_AGE_HOURS  cache TTL in hours (default 24)
#   BASH_TEST_FAKE_WORKTREE=1      skip the detached worktree dance
#                                   (used by the test harness)
#   BASH_TEST_CWD=<path>           cwd to chdir into before running each
#                                   harness (default: $REPO_ROOT; the
#                                   detached worktree overrides this)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$REPO_ROOT/.claude/state"
BASELINE="${BASH_TEST_BASELINE_PATH:-$STATE_DIR/bash-test-baseline.txt}"
MAX_AGE_HOURS="${BASH_TEST_BASELINE_MAX_AGE_HOURS:-24}"
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
# even if the detached worktree dance has since failed (e.g. transient
# network), so read-only callers ("is there a baseline?") always succeed.
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
# In fake-worktree mode the harness dir is user-supplied (BASH_TEST_CWD);
# we don't want to bail just because the test harness builds it into a
# tmpdir. So defer the directory existence check until after we've
# resolved HARNESS_DIR below.

if [ -z "${BASH_TEST_FAKE_WORKTREE:-}" ]; then
    if ! git -C "$REPO_ROOT" rev-parse --verify --quiet origin/master >/dev/null; then
        echo "error: origin/master not found locally — 'git fetch origin' first" >&2
        exit 1
    fi
fi

# Capture: bash every harness on a (possibly fake) detached worktree -------
# The detached worktree keeps the engineer's in-flight worktree completely
# untouched — no stash, no checkout dance. BASH_TEST_FAKE_WORKTREE=1 lets
# the test harness inject a synthetic cwd instead of a real worktree.
TMP=$(mktemp -d)
# Slot name MUST be unique per session: git derives the `.git/worktrees/<name>`
# entry from the path's basename, so a fixed name (e.g. `m`) collides under
# concurrent runs of this script (or with the pytest-$$ sibling script
# sharing the same repo). `bash-test-$$` guarantees a fresh slot per
# invocation. Same lesson as kanban card c23dfe46.
SLOT="bash-test-$$"
trap 'git -C "$REPO_ROOT" worktree remove --force --quiet "$TMP/$SLOT" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

# In fake-worktree mode, both the harness discovery glob AND the execution
# cwd point at the user-supplied BASH_TEST_CWD (the test harness builds a
# synthetic `scripts/` dir there). In real mode, both point at the
# detached worktree so we never touch the engineer's in-flight checkout.
if [ -z "${BASH_TEST_FAKE_WORKTREE:-}" ]; then
    if ! git -C "$REPO_ROOT" worktree add --detach "$TMP/$SLOT" origin/master >/dev/null; then
        echo "error: could not create detached worktree of origin/master" >&2
        exit 1
    fi
    HARNESS_DIR="$TMP/$SLOT/scripts"
    BASH_TEST_CWD="$TMP/$SLOT"
else
    BASH_TEST_CWD="${BASH_TEST_CWD:-$REPO_ROOT}"
    HARNESS_DIR="$BASH_TEST_CWD/scripts"
fi

if [ ! -d "$HARNESS_DIR" ]; then
    echo "error: $HARNESS_DIR not found — no test_*.sh harnesses to capture" >&2
    exit 1
fi

mkdir -p "$(dirname "$BASELINE")"
: > "$BASELINE.tmp"

# Run each harness. Harness exit codes are non-zero on failure — that is
# not an error here, it is the signal we capture. `set +e` + `|| true`
# swallows it the same way scripts/pytest-baseline.sh does.
#
# Per-harness: parse `  FAIL: <desc>` lines (the leading two-space indent
# matches the project convention `bad() { echo "  FAIL: $1"; … }`). If a
# harness produces zero FAIL lines AND emits a bash parse error in stderr,
# synthesize a single sentinel so downstream attribution still works.
#
# BASH_TEST_SKIP is an extended-regex (grep -E) of harness basenames to
# skip — useful for harnesses that spawn real services (e.g. test_cockpit.sh
# starts the backend) or are known-redundant in a comparison context.
set +e
for harness in "$HARNESS_DIR"/test_*.sh; do
    [ -f "$harness" ] || continue
    name="$(basename "$harness")"
    if [ -n "${BASH_TEST_SKIP:-}" ] && printf '%s' "$name" | grep -qE "$BASH_TEST_SKIP"; then
        continue
    fi
    out="$( cd "$BASH_TEST_CWD" && bash "$harness" 2>&1 )" || true

    fails="$(printf '%s\n' "$out" | grep -E '^  FAIL: ' | sed -E 's/^  FAIL: //')"

    if [ -z "$fails" ]; then
        err="$( cd "$BASH_TEST_CWD" && bash "$harness" 2>&1 1>/dev/null )" || true
        # Bash emits parse errors as `<file>: line N: syntax error: ...`,
        # NOT `bash: ...` — see bash(1) PARSE ERROR FORMAT. Cover both forms
        # so the harness works whether bash prefixes with `bash:` (e.g. when
        # invoked via `bash <file>` from an interactive shell) or with the
        # file path (the default when bash is invoked with a positional
        # script). Also catch "unbound variable" so `set -u` triggers
        # qualify.
        if printf '%s' "$err" | grep -qE '(bash:.*)?(syntax error|unexpected end of file|unbound variable|command not found|No such file)'; then
            fails="$name: harness crashed without FAIL lines (exit code non-zero)"
        fi
    fi

    while IFS= read -r line; do
        [ -n "$line" ] && printf '%s\tFAIL: %s\n' "$name" "$line" >> "$BASELINE.tmp"
    done <<< "$fails"
done
set -e

sort -u "$BASELINE.tmp" > "$BASELINE"
rm -f "$BASELINE.tmp"
n=$(wc -l < "$BASELINE")
echo "Captured baseline: $n pre-existing failures → $BASELINE"