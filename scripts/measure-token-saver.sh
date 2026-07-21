#!/usr/bin/env bash
# Token-saver measurement harness.
#
# End-to-end recipe for K1 of the 9router follow-ups
# (docs/cockpit/9router-integratie-analyse.md §9): measure token consumption
# AND quality regression on a real Cockpit dispatch workload, with and
# without a prompt-mutating saver applied.
#
# Subcommands:
#   compare   — default; runs baseline + with-saver, prints a 3-row table
#   baseline  — runs only the no-saver variant
#   with-saver — runs only the saver-mutated variant
#
# See docs/cockpit/token-saver-meet-harnas.md for the design rationale.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/measure_token_saver_lib.sh"

CMD="${1:-compare}"
case "$CMD" in
    baseline|with-saver|compare) ;;
    -h|--help|help)
        cat <<EOF
Usage: $0 [baseline|with-saver|compare]

Subcommands:
  compare     default; runs both baseline and with-saver, prints one table
  baseline    runs only the no-saver variant
  with-saver  runs only the saver-mutated variant

Output: a 3-row Markdown table (variant | input | cache_creation |
cache_read | output | pass_tests | pass_diff).

The harness creates a scratch git worktree, reverts commit b30a9bb's
one-character fix in backend/app/kanban/dispatch.py, spawns 'claude -p' once
per variant against the golden-task prompt, captures usage, and scores the
result via pytest + git diff. The scratch worktree is removed on exit.

Requires: claude CLI on PATH, git, pytest (via venv or system), network for
the initial 'git fetch origin master' (offline tolerated; falls back to
local master).
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

# Resolve repo + scratch worktree
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WT="$(mktemp -d -p "$REPO_ROOT")/wt-$$"
trap 'cleanup_worktree "$REPO_ROOT" "$WT"; rmdir "$(dirname "$WT")" 2>/dev/null || true' EXIT

WT=$( make_worktree "$REPO_ROOT" "$WT" )

# Apply the "revert" — set the dispatch.py line to the broken `> 0` state.
# b30a9bb's tests already live on master, so we only need to flip the one
# line in the working tree.
sed -i 's/r.max_sessions >= 0/r.max_sessions > 0/' "$WT/backend/app/kanban/dispatch.py"

# Build the deterministic prompt
PROMPT_FILE="$WT/.measure-prompt.txt"
build_prompt "$WT" > "$PROMPT_FILE"

# Resolve pytest command (uses shared venv per worktree convention)
BACKEND_DIR="$WT/backend"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/resolve-pytest-cmd.sh" 2>/dev/null || true
if ! resolve_pytest_cmd "$BACKEND_DIR" 2>/dev/null; then
    PYTEST_CMD=""
fi

# Empty mcp-config so the worktree has zero MCP servers — minimal baseline.
EMPTY_MCP="$WT/.mcp-empty.json"
printf '{"mcpServers":{}}' > "$EMPTY_MCP"

run_one() {
    local variant="$1"
    local out_json="$WT/${variant}.json"
    local out_err="$WT/${variant}.err"
    local score_out="$WT/${variant}.score"
    local raw_prompt="$PROMPT_FILE"
    if [ "$variant" = "with-saver" ]; then
        local mutated="$PROMPT_FILE.mutated"
        apply_saver "$PROMPT_FILE" "$mutated"
        raw_prompt="$mutated"
    fi
    (
        cd "$WT"
        timeout 300 claude -p \
            --dangerously-skip-permissions \
            --output-format json \
            --model "${CLAUDE_MODEL:-sonnet}" \
            --strict-mcp-config --mcp-config "$EMPTY_MCP" \
            < "$raw_prompt" > "$out_json" 2> "$out_err"
    )
    echo $? > "$WT/${variant}.exit"
    if [ -s "$out_json" ]; then
        parse_usage "$out_json" > "$WT/${variant}.usage" 2> "$WT/${variant}.usage.err" || true
    fi
    score_golden "$WT" > "$score_out" 2> "$WT/${variant}.score.err" || true
}

emit_table() {
    # Header
    printf '| %-10s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
        variant input cache_creation cache_read output pass_tests pass_diff
    printf '|------------|--------------|------------------|--------------|----------|-------------|------------|\n'

    for variant in baseline with-saver; do
        local usage_file="$WT/${variant}.usage"
        local score_file="$WT/${variant}.score"
        if [ -s "$usage_file" ]; then
            local input cc cr out
            input=$(sed -n 1p "$usage_file")
            cc=$(sed -n 2p "$usage_file")
            cr=$(sed -n 3p "$usage_file")
            out=$(sed -n 4p "$usage_file")
        else
            input="?"; cc="?"; cr="?"; out="?"
        fi
        local pt pd
        if [ -s "$score_file" ]; then
            pt=$(grep '^pass_tests=' "$score_file" | cut -d= -f2)
            pd=$(grep '^pass_diff=' "$score_file" | cut -d= -f2)
        else
            pt="?"; pd="?"
        fi
        printf '| %-10s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
            "$variant" "$input" "$cc" "$cr" "$out" "$pt" "$pd"
    done

    # Delta row (compute inline; uses string-safe arithmetic with -- + 0 trick)
    if [ -s "$WT/baseline.usage" ] && [ -s "$WT/with-saver.usage" ]; then
        printf '| %-10s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
            delta \
            "$(( $(sed -n 1p "$WT/with-saver.usage") - $(sed -n 1p "$WT/baseline.usage") ))" \
            "$(( $(sed -n 2p "$WT/with-saver.usage") - $(sed -n 2p "$WT/baseline.usage") ))" \
            "$(( $(sed -n 3p "$WT/with-saver.usage") - $(sed -n 3p "$WT/baseline.usage") ))" \
            "$(( $(sed -n 4p "$WT/with-saver.usage") - $(sed -n 4p "$WT/baseline.usage") ))" \
            "—" "—"
    fi
}

case "$CMD" in
    baseline)
        run_one baseline
        emit_table
        ;;
    with-saver)
        run_one with-saver
        emit_table
        ;;
    compare)
        run_one baseline
        run_one with-saver
        emit_table
        ;;
esac