#!/usr/bin/env bash
# Run every scripts/test_*.sh in the current worktree and classify each
# failure as "pre-existing on origin/master" (not your fault) vs "new since
# your changes started" (your fault). Companion to
# scripts/baseline-bash-tests.sh, which seeds the cache this script diffs
# against.
#
# Usage:
#   scripts/compare-bash-tests.sh                  # run bash tests + classify
#   scripts/compare-bash-tests.sh --pre-existing-only   # just the count + list
#   scripts/compare-bash-tests.sh --help
#
# Exits:
#   0  no NEW failures (only pre-existing)  — clean
#   1  one or more NEW failures             — engineer needs to fix
#   2  no baseline cached yet               — caller forgot `baseline-bash-tests.sh`
#
# This script is meant to be invoked either standalone (when an engineer
# wants a quick triage) or wired into iteration-loop as the
# `bash-test-attr` preset that gates an engineer card.
#
# Output: per-harness grouping for NEW / FIXED sections so the engineer
# can read across 16 harnesses at a glance. The pre-existing section is
# still a single count + caveat (matches pytest-compare.sh shape).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASELINE="${BASH_TEST_BASELINE_PATH:-$REPO_ROOT/.claude/state/bash-test-baseline.txt}"
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
    echo "error: no baseline at $BASELINE — run scripts/baseline-bash-tests.sh first" >&2
    exit 2
fi

# Same capture pipeline as baseline-bash-tests.sh, but writes to a tmp
# file we can diff against the baseline. BASH_TEST_CWD overrides let the
# test harness inject a synthetic cwd without paying for a real detached
# worktree dance.
BASH_TEST_CWD="${BASH_TEST_CWD:-$REPO_ROOT}"
HARNESS_DIR="$BASH_TEST_CWD/scripts"

CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT" "$CURRENT.tmp"' EXIT
: > "$CURRENT.tmp"

# Bash tests exit non-zero on failure — that is the signal we capture, not
# an error. `set +e` + `|| true` swallows it (same pattern as
# pytest-compare.sh:74-81).
#
# BASH_TEST_SKIP is an extended-regex (grep -E) of harness basenames to
# skip — useful for harnesses that spawn real services or are
# known-redundant in a comparison context. Same shape as in
# baseline-bash-tests.sh.
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
        [ -n "$line" ] && printf '%s\tFAIL: %s\n' "$name" "$line" >> "$CURRENT.tmp"
    done <<< "$fails"
done
set -e

sort -u "$CURRENT.tmp" > "$CURRENT"

# Set arithmetic: |pre-existing ∩ current|, |current \ pre-existing|,
# |pre-existing \ current|. Use comm -12 (intersection), -23 (current-only),
# -13 (baseline-only). Both files are sorted + unique, so this is exact.
# Same math as pytest-compare.sh:86-89.
pre_count=$(comm -12 "$BASELINE" "$CURRENT" | wc -l | tr -d ' ')
new_list=$(comm -23 "$CURRENT" "$BASELINE" || true)
fixed_list=$(comm -13 "$CURRENT" "$BASELINE" || true)
new_count=$(printf '%s\n' "$new_list" | grep -c . || true)

# --pre-existing-only: cheapest output path. Triage-tool mode.
if [ "$PRE_ONLY" = 1 ]; then
    echo "pre-existing failures (not your fault): $pre_count"
    echo "  ^ these harnesses are still FAILING — \"pre-existing\" means the failure"
    echo "    is already on origin/master too, NOT that the test passes. Read"
    echo "    the FAIL: line before writing it off as environmental."
    exit 0
fi

echo "=== bash-test failure attribution ==="
echo "pre-existing (not your fault): $pre_count"
if [ "$pre_count" -gt 0 ]; then
    echo "  ^ these harnesses are still FAILING — \"pre-existing\" means the failure"
    echo "    is already on origin/master too, NOT that the test passes. Read"
    echo "    the FAIL: line before writing it off as environmental."
    # Print the unique harness names with at least one pre-existing failure so
    # the operator can grep for a specific harness without scrolling. This is
    # much cheaper than the per-harness grouped NEW/FIXED listing, and
    # matches the "triage-shape" the pre-existing section already claims.
    comm -12 "$BASELINE" "$CURRENT" \
        | cut -f1 | sort -u \
        | sed 's/^/    /' \
        | sed '1i\  affected harnesses:'
fi

# Per-harness grouping: buffer NEW lines keyed on the harness-name prefix
# (column 1 of the tab-separated baseline row). The `awk` end block prints
# one header + indented list per harness, sorted by harness name.
group_by_harness() {
    awk -F'\t' '
        { h[$1] = h[$1] ? h[$1] "\n    " $2 : "    " $2 }
        END { for (k in h) print "  " k "\n" h[k] }
    ' | sort
}

if [ "$new_count" -gt 0 ]; then
    echo "NEW (your fault — needs fix):"
    printf '%s\n' "$new_list" | group_by_harness
fi

if [ -n "$fixed_list" ]; then
    echo "FIXED by your changes:"
    printf '%s\n' "$fixed_list" | group_by_harness
fi

[ "$new_count" -eq 0 ]