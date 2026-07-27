#!/usr/bin/env bash
# Run ruff in the current worktree and classify each hit as
# "pre-existing on origin/master" (not your fault) vs "new since your
# changes started" (your fault). Companion to scripts/ruff-baseline.sh,
# which seeds the cache that this script diffs against.
#
# Usage:
#   scripts/ruff-compare.sh                  # run ruff + classify
#   scripts/ruff-compare.sh --pre-existing-only   # just the count + list
#   scripts/ruff-compare.sh --help
#
# Exits:
#   0  no NEW hits (only pre-existing)  — clean
#   1  one or more NEW hits             — engineer needs to fix
#   2  no baseline cached yet           — caller forgot `ruff-baseline.sh`
#
# This script is meant to be invoked either standalone (when an engineer
# wants a quick triage) or wired into iteration-loop as the `ruff-attr`
# preset that gates an engineer card. Kanban card 7678afc4… is the
# precedent (option B of the original card, picked because option A's
# "fix all 31 hits now" only solves today's hits, not tomorrow's).
#
# Caveat — the baseline is `backend/app backend/tests`-scoped, the same
# set the project's `quality.yml` gate runs. If you lint a wider tree
# (e.g. `ruff check .`) the comparator will see every extra hit as
# "NEW" — keep the scope identical to what the baseline captured.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
BASELINE="${RUFF_BASELINE_PATH:-$REPO_ROOT/.claude/state/ruff-baseline.txt}"
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
    echo "error: no baseline at $BASELINE — run scripts/ruff-baseline.sh first" >&2
    exit 2
fi

# RUFF_CMD (with optional RUFF_CWD) lets the test harness fake ruff for
# isolation tests without touching the real venv. Absent an override, falls
# back through worktree-local venv → shared main-checkout venv → PATH (same
# chain as scripts/ruff-compare.sh's pytest sibling).
source "$SCRIPT_DIR/lib/resolve-ruff-cmd.sh"
if ! resolve_ruff_cmd "$BACKEND_DIR"; then
    exit 1
fi
RUFF_CWD="${RUFF_CWD:-$BACKEND_DIR}"
if [ ! -x "$RUFF_CMD" ]; then
    echo "error: $RUFF_CMD not executable — run scripts/install.sh first" >&2
    exit 1
fi

# Capture current hits into a tmp file we can diff against the baseline.
CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT

# Ruff exits non-zero on hit; that exit code is *not* an error here, it's
# the signal we want to capture. Disable -e around this block. Same pattern
# as scripts/pytest-compare.sh:73-81.
set +e
(
    cd "$RUFF_CWD"
    "$RUFF_CMD" check --output-format=concise app tests 2>&1
) | grep -E ':[0-9]+:[0-9]+: ' \
    | sort -u > "$CURRENT"
set -e

# Set arithmetic: |pre-existing ∩ current|, |current \ pre-existing|,
# |pre-existing \ current|. Use comm -12 (intersection), -23 (current-only),
# -13 (baseline-only). Both files are sorted + unique, so this is exact.
# Same math as scripts/pytest-compare.sh:86-89.
pre_count=$(comm -12 "$BASELINE" "$CURRENT" | wc -l | tr -d ' ')
new_list=$(comm -23 "$CURRENT" "$BASELINE" || true)
fixed_list=$(comm -13 "$CURRENT" "$BASELINE" || true)
new_count=$(printf '%s\n' "$new_list" | grep -c . || true)

# --pre-existing-only: cheapest output path. Triage-tool mode.
if [ "$PRE_ONLY" = 1 ]; then
    echo "pre-existing failures (not your fault): $pre_count"
    echo "  ^ these hits are still PRESENT — \"pre-existing\" means the hit"
    echo "    is already on origin/master too, NOT that ruff passes. Read"
    echo "    the line before writing it off as environmental."
    exit 0
fi

echo "=== ruff hit attribution ==="
echo "pre-existing (not your fault): $pre_count"
if [ "$pre_count" -gt 0 ]; then
    echo "  ^ these hits are still PRESENT — \"pre-existing\" means the hit"
    echo "    is already on origin/master too, NOT that ruff passes. Read"
    echo "    the line before writing it off as environmental."
fi
# Ruff messages contain spaces (e.g. "F401 [*] `foo` imported but unused"),
# so a bare `printf '  %s\n' $new_list` would word-split the line and
# render each token on its own row. The pytest equivalent is safe only
# because pytest test-IDs don't contain spaces; for ruff we loop per
# line and quote the format arg. Same shape as compare-bash-tests.sh
# but applied to the unattributed list here.
if [ -n "$new_list" ] && [ "$new_count" -gt 0 ]; then
    echo "NEW (your fault — needs fix):"
    while IFS= read -r line; do
        [ -n "$line" ] && printf '  %s\n' "$line"
    done <<< "$new_list"
fi
if [ -n "$fixed_list" ]; then
    echo "FIXED by your changes:"
    while IFS= read -r line; do
        [ -n "$line" ] && printf '  %s\n' "$line"
    done <<< "$fixed_list"
fi

[ "$new_count" -eq 0 ]
