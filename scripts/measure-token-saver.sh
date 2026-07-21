#!/usr/bin/env bash
# Token-saver measurement harness.
#
# End-to-end recipe for K1 of the 9router follow-ups
# (docs/cockpit/9router-integratie-analyse.md §9): measure token consumption
# AND quality regression on a real Cockpit dispatch workload, with and
# without a prompt-mutating saver applied.
#
# Subcommands:
#   compare   — default; runs two counterbalanced baseline/saver trials
#   baseline  — runs only the no-saver variant
#   with-saver — runs only the saver-mutated variant
#
# See docs/cockpit/token-saver-meet-harnas.md for the design rationale.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/measure_token_saver_lib.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/worktree-trap.sh"

CMD="${1:-compare}"
case "$CMD" in
    baseline|with-saver|compare) ;;
    -h|--help|help)
        cat <<EOF
Usage: $0 [baseline|with-saver|compare]

Subcommands:
  compare     default; runs two isolated trials in counterbalanced order
  baseline    runs one isolated no-saver variant
  with-saver  runs one isolated saver-mutated variant

Output: a Markdown table with one row per trial/variant and separate input,
cache_creation, cache_read, output, pass_tests, and pass_diff columns.

The harness creates a fresh scratch git worktree and reapplies the
backend/app/kanban/dispatch.py golden-task revert for every variant. In
compare mode it runs baseline→with-saver, then with-saver→baseline, so neither
worktree state nor variant order is a confounder. Scratch worktrees are removed
on exit.

Requires: claude CLI on PATH, git, pytest (via venv or system). Each variant
runs in its own detached scratch worktree created from the resolved baseline
ref (origin/master → master → HEAD) of $REPO_ROOT, so no network is required;
the only filesystem footprint between invocations is the result directory
printed at the end of the run.

Results land in $MEASURE_RESULT_DIR (defaults to
$REPO_ROOT/.tmp-measure-token-saver/<timestamp>/). Pass an explicit
absolute path to MEASURE_RESULT_DIR to capture artifacts in a known place.
EOF
        exit 0
        ;;
    *)
        echo "error: unknown subcommand '$CMD' (expected baseline|with-saver|compare)" >&2
        exit 2
        ;;
esac

# Pre-flight
command -v claude >/dev/null 2>&1 || {
    echo "error: 'claude' CLI not on PATH" >&2
    exit 3
}
claude --version >/dev/null 2>&1 || {
    echo "error: 'claude --version' failed" >&2
    exit 3
}

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve the baseline ref once. Every per-trial worktree is detached from the
# same $BASE_REF so all four counterbalanced trials start from an identical,
# reproducible state (no leaking of HEAD from a prior trial).
BASE_REF="$(resolve_measurement_base_ref "$REPO_ROOT")"

# Result directory — caller-visible so artifacts aren't dropped into /tmp.
# Default: $REPO_ROOT/.tmp-measure-token-saver/<UTC-timestamp>/. Set
# MEASURE_RESULT_DIR to override (an existing directory is refused so we
# never clobber prior runs). On exit the path is printed so the operator
# can `ls` / copy files from it; the directory itself is kept (not
# auto-deleted) so results survive the script ending.
if [ -n "${MEASURE_RESULT_DIR:-}" ]; then
    if [ -e "$MEASURE_RESULT_DIR" ]; then
        echo "error: MEASURE_RESULT_DIR already exists: $MEASURE_RESULT_DIR" >&2
        exit 4
    fi
    RESULT_DIR="$MEASURE_RESULT_DIR"
else
    RESULT_DIR="$REPO_ROOT/.tmp-measure-token-saver/$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$RESULT_DIR"

# Each call owns one fresh worktree. The result files are copied out before
# cleanup, because the worktree is deliberately not shared by later variants.
run_one() {
    local trial="$1" variant="$2"
    local wt_path_file wt prompt_file empty_mcp backend_dir
    local result_prefix="$RESULT_DIR/trial-${trial}-${variant}"
    wt_path_file="$(mktemp)"

    with_scratch_worktree "$REPO_ROOT" WT "$BASE_REF" > "$wt_path_file"
    wt="$(cat "$wt_path_file")"
    rm -f "$wt_path_file"

    # Reapply the golden-task revert independently in every scratch worktree.
    # prepare_golden_revert fails closed if the baseline lacks the fixed line,
    # so a no-op measurement never reaches the table.
    if ! prepare_golden_revert "$wt"; then
        cleanup_scratch_worktree "$REPO_ROOT" "$wt"
        echo "error: prepare_golden_revert failed for trial=$trial variant=$variant" >&2
        return 1
    fi

    prompt_file="$wt/.measure-prompt.txt"
    build_prompt "$wt" > "$prompt_file"
    backend_dir="$wt/backend"
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/lib/resolve-pytest-cmd.sh" 2>/dev/null || true
    if ! resolve_pytest_cmd "$backend_dir" 2>/dev/null; then
        PYTEST_CMD=""
    fi

    empty_mcp="$wt/.mcp-empty.json"
    printf '{"mcpServers":{}}' > "$empty_mcp"

    local raw_prompt="$prompt_file"
    if [ "$variant" = "with-saver" ]; then
        local mutated="$prompt_file.mutated"
        apply_saver "$prompt_file" "$mutated"
        raw_prompt="$mutated"
    fi

    (
        cd "$wt"
        timeout 300 claude -p \
            --dangerously-skip-permissions \
            --output-format json \
            --model "${CLAUDE_MODEL:-sonnet}" \
            --strict-mcp-config --mcp-config "$empty_mcp" \
            < "$raw_prompt" > "${result_prefix}.json" \
            2> "${result_prefix}.err"
    )
    echo $? > "${result_prefix}.exit"
    if [ -s "${result_prefix}.json" ]; then
        parse_usage "${result_prefix}.json" > "${result_prefix}.usage" \
            2> "${result_prefix}.usage.err" || true
    fi
    BACKEND_DIR="$backend_dir" \
        score_golden "$wt" > "${result_prefix}.score" \
        2> "${result_prefix}.score.err" || true

    cleanup_scratch_worktree "$REPO_ROOT" "$wt"
}

emit_row() {
    local label="$1"
    local usage_file="$RESULT_DIR/${label}.usage"
    local score_file="$RESULT_DIR/${label}.score"
    local input cc cr out pt pd
    if [ -s "$usage_file" ]; then
        input=$(sed -n 1p "$usage_file")
        cc=$(sed -n 2p "$usage_file")
        cr=$(sed -n 3p "$usage_file")
        out=$(sed -n 4p "$usage_file")
    else
        input="?"; cc="?"; cr="?"; out="?"
    fi
    if [ -s "$score_file" ]; then
        pt=$(grep '^pass_tests=' "$score_file" | cut -d= -f2)
        pd=$(grep '^pass_diff=' "$score_file" | cut -d= -f2)
    else
        pt="?"; pd="?"
    fi
    printf '| %-18s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
        "$label" "$input" "$cc" "$cr" "$out" "$pt" "$pd"
}

emit_delta() {
    local trial="$1" baseline="$RESULT_DIR/trial-${trial}-baseline.usage"
    local saver="$RESULT_DIR/trial-${trial}-with-saver.usage"
    if [ -s "$baseline" ] && [ -s "$saver" ]; then
        printf '| %-18s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
            "trial-${trial}-delta" \
            "$(( $(sed -n 1p "$saver") - $(sed -n 1p "$baseline") ))" \
            "$(( $(sed -n 2p "$saver") - $(sed -n 2p "$baseline") ))" \
            "$(( $(sed -n 3p "$saver") - $(sed -n 3p "$baseline") ))" \
            "$(( $(sed -n 4p "$saver") - $(sed -n 4p "$baseline") ))" \
            "—" "—"
    fi
}

emit_table() {
    printf '| %-18s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
        label input cache_creation cache_read output pass_tests pass_diff
    printf '|--------------------|--------------|------------------|--------------|----------|-------------|------------|\n'
    local trial variant
    for trial in "$@"; do
        for variant in baseline with-saver; do
            if [ -s "$RESULT_DIR/trial-${trial}-${variant}.json" ] \
                || [ -s "$RESULT_DIR/trial-${trial}-${variant}.score" ]; then
                emit_row "trial-${trial}-${variant}"
            fi
        done
        if [ "$trial" -gt 0 ]; then
            emit_delta "$trial"
        fi
    done
}

case "$CMD" in
    baseline)
        run_one 1 baseline
        emit_table 1
        ;;
    with-saver)
        run_one 1 with-saver
        emit_table 1
        ;;
    compare)
        run_one 1 baseline
        run_one 1 with-saver
        run_one 2 with-saver
        run_one 2 baseline
        emit_table 1 2
        ;;
esac

# Print a discoverability footer so artifacts are findable.
printf '\n# artifacts: %s\n' "$RESULT_DIR"
printf '# files: %s\n' "$(ls -1 "$RESULT_DIR" 2>/dev/null | wc -l | tr -d ' ')"
