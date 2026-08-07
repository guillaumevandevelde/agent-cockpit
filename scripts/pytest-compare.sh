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
#
# Caveat — the baseline is FULL-SUITE-scoped (see pytest-baseline.sh's
# top-of-file note + kanban card 446efe9b). A test that is red only in a
# targeted/subset run because of ordering between test files will not
# appear in the baseline, so this script will report it as NEW even
# though it isn't your regression. If a NEW failure surprises you, re-run
# that test inside the full suite (`pytest` with no path filter, or
# `scripts/pytest-baseline.sh --regen` then a fresh full-suite run) to
# distinguish an ordering artefact from a real one.

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

# Strip the `# `-prefixed metadata header (written by pytest-baseline.sh at
# capture time — see kanban card 53af2e23…) before running `comm`, otherwise
# the header lines would either pollute the diff or be reported as fake test
# names. `comm` requires sorted input; the body is already `sort -u`'d at
# capture, and the header removal preserves that ordering.
BASELINE_NO_META="$(mktemp)"
trap 'rm -f "$CURRENT" "$BASELINE_NO_META"' EXIT
grep -v '^# ' "$BASELINE" > "$BASELINE_NO_META" || true

# Set arithmetic: |pre-existing ∩ current|, |current \ pre-existing|,
# |pre-existing \ current|. Use comm -12 (intersection), -23 (current-only),
# -13 (baseline-only). Both files are sorted + unique, so this is exact.
pre_count=$(comm -12 "$BASELINE_NO_META" "$CURRENT" | wc -l)
new_list=$(comm -23 "$CURRENT" "$BASELINE_NO_META" || true)
fixed_list=$(comm -13 "$CURRENT" "$BASELINE_NO_META" || true)
new_count=$(printf '%s\n' "$new_list" | grep -c . || true)

# Baseline provenance — printed in BOTH modes (incl. --pre-existing-only) so
# the engineer can read a "pre-existing: 5" line in the context of "baseline
# was captured N hours ago on origin/master@SHA, which has since moved M
# commits". A "0 NEW" line without this context is exactly the ambiguity
# that burned kaart ae9648c2… (kanban card 53af2e23…).
body_count=$(grep -cv '^# ' "$BASELINE" || true)
meta_captured_at=$(grep -m1 '^# captured-at: ' "$BASELINE" 2>/dev/null \
    | sed -E 's/^# captured-at:[[:space:]]+//' || true)
meta_baseline_sha=$(grep -m1 '^# baseline-sha: ' "$BASELINE" 2>/dev/null \
    | sed -E 's/^# baseline-sha:[[:space:]]+//' || true)
echo "=== pytest baseline context ==="
if [ -n "$meta_baseline_sha" ]; then
    echo "baseline: $body_count pre-existing failures (captured ${meta_captured_at:-unknown}, baseline-sha: $meta_baseline_sha)"
else
    echo "baseline: $body_count pre-existing failures (no baseline-sha recorded — legacy file, re-run pytest-baseline.sh --regen)"
fi
# Compare against current origin/master so a stale cache can't be silently
# shipped. `rev-parse --verify --quiet` is the cheap, network-free check
# (master has to be fetched separately — we trust the dispatcher / the
# engineer's normal sync flow to have done that already).
if git -C "$REPO_ROOT" rev-parse --verify --quiet origin/master >/dev/null 2>&1; then
    current_sha="$(git -C "$REPO_ROOT" rev-parse origin/master)"
    if [ -n "$meta_baseline_sha" ]; then
        if [ "$meta_baseline_sha" = "$current_sha" ]; then
            echo "origin/master: $current_sha [matches baseline]"
        else
            # `rev-list --count A..B` enumerates commits in B not in A. We
            # want "how far origin/master has moved past the baseline-sha",
            # i.e. commits in origin/master that aren't in baseline-sha, so
            # A=baseline_sha, B=origin/master.
            behind="$(git -C "$REPO_ROOT" rev-list --count "$meta_baseline_sha..origin/master" 2>/dev/null || echo "?")"
            echo "origin/master: $current_sha [STALE — baseline is $behind commits behind]"
        fi
    else
        echo "origin/master: $current_sha (no baseline-sha recorded to compare against)"
    fi
fi

# --pre-existing-only: cheapest output path. Triage-tool mode.
if [ "$PRE_ONLY" = 1 ]; then
    echo "pre-existing failures (not your fault): $pre_count"
    echo "  ^ these tests are still FAILING — \"pre-existing\" means the failure"
    echo "    is already on origin/master too, NOT that the test passes. Read"
    echo "    the traceback before writing it off as environmental."
    exit 0
fi

echo "=== pytest failure attribution ==="
echo "pre-existing (not your fault): $pre_count"
if [ "$pre_count" -gt 0 ]; then
    echo "  ^ these tests are still FAILING — \"pre-existing\" means the failure"
    echo "    is already on origin/master too, NOT that the test passes. Read"
    echo "    the traceback before writing it off as environmental."
fi
if [ -n "$new_list" ] && [ "$new_count" -gt 0 ]; then
    echo "NEW (your fault — needs fix):"
    printf '  %s\n' $new_list
fi
if [ -n "$fixed_list" ]; then
    echo "FIXED by your changes:"
    printf '  %s\n' $fixed_list
fi

[ "$new_count" -eq 0 ]
