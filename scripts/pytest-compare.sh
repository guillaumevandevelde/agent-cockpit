#!/usr/bin/env bash
# Run pytest in the current worktree and classify each failure as
# "pre-existing on origin/master" (not your fault) vs "new since your
# changes started" (your fault). Companion to scripts/pytest-baseline.sh,
# which seeds the cache that this script diffs against.
#
# Usage:
#   scripts/pytest-compare.sh                  # run pytest + classify
#   scripts/pytest-compare.sh --pre-existing-only   # just the count + list
#   scripts/pytest-compare.sh --help
#
# Exits:
#   0  no NEW failures (only pre-existing)  — clean
#   1  one or more NEW failures             — engineer needs to fix
#   2  no baseline cached yet               — caller forgot `pytest-baseline.sh`
#
# This script is meant to be invoked either standalone (when an engineer
# wants a quick triage) or wired into iteration-loop as the
# `pytest-attr` preset that gates an engineer card.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BASELINE="${PYTEST_BASELINE_PATH:-$REPO_ROOT/.claude/state/pytest-baseline.txt}"
PRE_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --pre-existing-only) PRE_ONLY=1 ;;
        -h|--help)
            awk 'NR==1{next} /^$/{exit} {sub(/^# ?/,""); print}' "$0"
            exit 0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ ! -f "$BASELINE" ]; then
    echo "error: no baseline at $BASELINE — run scripts/pytest-baseline.sh first" >&2
    exit 2
fi

# PYTEST_CMD (with optional PYTEST_CWD) lets the test harness fake pytest for
# isolation tests without touching the real venv. Default points at the
# production venv.
PYTEST_CMD="${PYTEST_CMD:-$BACKEND_DIR/venv/bin/pytest}"
PYTEST_CWD="${PYTEST_CWD:-$BACKEND_DIR}"
if [ ! -x "$PYTEST_CMD" ]; then
    echo "error: $PYTEST_CMD not executable — run scripts/install.sh first" >&2
    exit 1
fi

# Capture current failures into a tmp file we can diff against the baseline.
CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT

# Pytest exits non-zero on failure; that exit code is *not* an error here,
# it's the signal we want to capture. Disable -e around this block.
set +e
(
    cd "$PYTEST_CWD"
    "$PYTEST_CMD" --tb=no -q 2>&1
) | grep -E '^(FAILED|ERROR) ' \
    | sed -E 's/^(FAILED|ERROR) +//; s/ +- .*$//' \
    | sort -u > "$CURRENT"
set -e

# Set arithmetic: |pre-existing ∩ current|, |current \ pre-existing|,
# |pre-existing \ current|. Use comm -12 (intersection), -23 (current-only),
# -13 (baseline-only). Both files are sorted + unique, so this is exact.
pre_count=$(comm -12 "$BASELINE" "$CURRENT" | wc -l)
new_list=$(comm -23 "$CURRENT" "$BASELINE" || true)
fixed_list=$(comm -13 "$CURRENT" "$BASELINE" || true)
new_count=$(printf '%s\n' "$new_list" | grep -c . || true)

# --pre-existing-only: cheapest output path. Triage-tool mode.
if [ "$PRE_ONLY" = 1 ]; then
    echo "pre-existing failures (not your fault): $pre_count"
    exit 0
fi

echo "=== pytest failure attribution ==="
echo "pre-existing (not your fault): $pre_count"
if [ -n "$new_list" ] && [ "$new_count" -gt 0 ]; then
    echo "NEW (your fault — needs fix):"
    printf '  %s\n' $new_list
fi
if [ -n "$fixed_list" ]; then
    echo "FIXED by your changes:"
    printf '  %s\n' $fixed_list
fi

[ "$new_count" -eq 0 ]
