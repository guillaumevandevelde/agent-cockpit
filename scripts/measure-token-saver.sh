#!/usr/bin/env bash
# Token-saver measurement harness.
#
# End-to-end recipe for K1 of the 9router follow-ups
# (docs/cockpit/9router-integratie-analyse.md §9): measure token consumption
# AND quality regression on a real Cockpit dispatch workload, with and
# without a prompt-mutating saver applied.
#
# Subcommands:
#   compare       — default; runs two counterbalanced trials with three variants
#   baseline      — runs only the no-saver variant
#   with-saver    — runs only the prompt-mutated proxy variant
#   card-baseline — runs only the production-shaped prompt with both injector
#                   kwargs empty (the lane-flags-off dispatch)
#   card-injector — runs only the production-shaped prompt with the verbatim
#                   Caveman + Ponytail slices in the injector kwargs
#   real-saver    — runs only the real-RTK-hook variant (requires RTK on PATH or
#                   COCKPIT_RTK_BIN; fails closed if no binary resolves)
#   injector-compare — the canonical kaart 5934b954… measurement: two
#                   counterbalanced trials of card-baseline vs card-injector.
#                   Both arms are rendered by the production
#                   backend/app/kanban/dispatch.py::build_card_prompt, so the
#                   only byte difference between them is the injector slices
#                   and `cache_read` is measured on the real dispatch prefix.
#
# See docs/cockpit/token-saver-meet-harnas.md for the design rationale and
# docs/superpowers/specs/2026-07-24-token-saver-integration-design.md §8.4
# for the `real-saver` variant's role in lockstep with the dispatch helper.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/measure_token_saver_lib.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/worktree-trap.sh"

CMD="${1:-compare}"
case "$CMD" in
    baseline|with-saver|card-baseline|card-injector|real-saver|compare|injector-compare) ;;
    -h|--help|help)
        cat <<EOF
Usage: $0 [baseline|with-saver|card-baseline|card-injector|real-saver|compare|injector-compare]

Subcommands:
  compare       default; runs two isolated trials in counterbalanced order with
                baseline / with-saver / real-saver variants
  injector-compare  runs two isolated counterbalanced trials of
                card-baseline vs card-injector. Both arms are rendered by the
                production build_card_prompt, so the only difference between
                them is the injector kwargs. This is the canonical run for
                kaart 5934b954... (cache_read on the real dispatch prefix).
  baseline      runs one isolated no-saver variant
  with-saver    runs one isolated saver-mutated variant (prompt-mutation proxy)
  card-baseline runs one isolated production-shaped prompt with both injector
                kwargs empty (what a lane with both flags off dispatches)
  card-injector runs one isolated production-shaped prompt with the verbatim
                Caveman + Ponytail slices from
                backend/app/kanban/prompt_injectors.py passed as the
                build_card_prompt injector kwargs. Fails closed (non-zero)
                when the production import cannot resolve.
  real-saver    runs one isolated variant with the actual RTK hook installed
                into the scratch worktree's .claude/settings.json. Fails closed
                if no RTK binary resolves (COCKPIT_RTK_BIN, cache, or PATH).

Output: a Markdown table with one row per trial/variant and separate input,
cache_creation, cache_read, output, pass_tests, and pass_diff columns.

The harness reapplies the backend/app/kanban/dispatch.py golden-task revert
for every variant. The proxy/RTK variants run in a fresh detached scratch
worktree; the card-shaped variants run in a git-less \`git archive\` export
under \$MEASURE_SANDBOX_ROOT (default \$HOME/.cache/cockpit-measure-sandbox),
because they carry the real ship recipe and an agent measured with it once
pushed its golden-task edit to origin/master. In compare mode each trial runs
baseline / with-saver / real-saver (in that order on trial 1, reverse on
trial 2) so neither tree state nor variant order is a confounder. In
injector-compare trial 1 runs card-baseline → card-injector and trial 2 the
reverse, so each arm gets one cold-cache and one warm-cache position.

Requires: claude CLI on PATH, git, pytest (via venv or system). Each variant
runs in its own detached scratch worktree created from the resolved baseline
ref (origin/master → master → HEAD) of \$REPO_ROOT, so no network is required;
the only filesystem footprint between invocations is the result directory
printed at the end of the run. The \`real-saver\` variant additionally requires
the RTK binary on PATH (or via COCKPIT_RTK_BIN).

Results land in \$MEASURE_RESULT_DIR (defaults to
\$REPO_ROOT/.tmp-measure-token-saver/<timestamp>/). Pass an explicit
absolute path to MEASURE_RESULT_DIR to capture artifacts in a known place.

MEASURE_TIMEOUT_S caps each run's wall clock (default 300). A run killed by
that timeout is reported as no-measurement — its row shows ? with the reason
underneath — because a partial transcript's token counters are not comparable
to a run that finished on its own. The card-shaped variants need more than
the default; injector-compare was validated at MEASURE_TIMEOUT_S=900.
EOF
        exit 0
        ;;
    *)
        echo "error: unknown subcommand '$CMD' (expected baseline|with-saver|card-baseline|card-injector|real-saver|compare|injector-compare)" >&2
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

# Per-run wall-clock ceiling. 300s fits the bare golden-task prompt, but the
# card-shaped variants carry the full dispatch prompt (persona + ship recipe),
# and an agent working through that legitimately needs longer — the first
# `injector-compare` attempt had the baseline arm finish in 78s while the
# injector arm was still working when the 300s kill landed. Raise this for the
# card-shaped variants; a killed run is reported as no-measurement rather than
# as a partial one (see the exit-124 branch in run_one).
RUN_TIMEOUT_S="${MEASURE_TIMEOUT_S:-300}"

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

# Sandbox root for the card-shaped variants. Outside $REPO_ROOT on purpose:
# a git-less export that still sat inside the repo would let a stray `git`
# call walk up into the real repository.
SANDBOX_ROOT="${MEASURE_SANDBOX_ROOT:-$HOME/.cache/cockpit-measure-sandbox}"
mkdir -p "$SANDBOX_ROOT"

# One releaser for both tree kinds, so every exit path in run_one cleans up
# whatever it actually created.
release_run_tree() {
    local sandboxed="$1" tree="$2"
    if [ "$sandboxed" = "1" ]; then
        cleanup_prompt_sandbox "$tree" || true
    else
        cleanup_scratch_worktree "$REPO_ROOT" "$tree"
    fi
}

# Each call owns one fresh worktree. The result files are copied out before
# cleanup, because the worktree is deliberately not shared by later variants.
run_one() {
    local trial="$1" variant="$2"
    local wt_path_file wt prompt_file empty_mcp backend_dir
    local result_prefix="$RESULT_DIR/trial-${trial}-${variant}"
    local sandboxed=0

    # Card-shaped variants get a git-less sandbox, not a scratch worktree.
    # They carry the real ship recipe, and an agent measured with it followed
    # that recipe all the way to `git push origin HEAD:master` on the shared
    # repo (kaart 5934b954…, reverted in 2e0eb256). A `git archive` export has
    # no .git, no remote and no credentials, and lives outside $REPO_ROOT, so
    # the ship step has nothing to act on. See make_prompt_sandbox.
    if [ "$variant" = "card-baseline" ] || [ "$variant" = "card-injector" ]; then
        sandboxed=1
        wt="$SANDBOX_ROOT/trial-${trial}-${variant}"
        cleanup_prompt_sandbox "$wt" 2>/dev/null || true
        if ! make_prompt_sandbox "$REPO_ROOT" "$BASE_REF" "$wt" 2> "${result_prefix}.sandbox.err"; then
            printf 'sandbox export failed: %s\n' \
                "$(tr '\n' ' ' < "${result_prefix}.sandbox.err")" > "${result_prefix}.missing"
            return 0
        fi
    else
        wt_path_file="$(mktemp)"
        with_scratch_worktree "$REPO_ROOT" WT "$BASE_REF" > "$wt_path_file"
        wt="$(cat "$wt_path_file")"
        rm -f "$wt_path_file"
    fi

    # Reapply the golden-task revert independently in every scratch tree.
    # prepare_golden_revert fails closed if the baseline lacks the fixed line,
    # so a no-op measurement never reaches the table.
    if ! prepare_golden_revert "$wt"; then
        release_run_tree "$sandboxed" "$wt"
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
    elif [ "$variant" = "card-baseline" ] || [ "$variant" = "card-injector" ]; then
        # Production-shaped prompt: the golden task goes in as the card
        # description and the whole thing is assembled by the real
        # build_card_prompt (persona → injector slices → card body → ship
        # instructions). The two variants differ ONLY in the injector kwargs,
        # so the measured delta is the slice and nothing else.
        #
        # Fail-closed like real-saver: a stub prompt here would produce
        # numbers that do not describe the dispatch shape, which is the bug
        # this variant pair exists to fix (kaart 5934b954…, impediment 2).
        local inject=0
        [ "$variant" = "card-injector" ] && inject=1
        local rendered="$prompt_file.card-$inject"
        if ! render_card_prompt "$prompt_file" "$rendered" "$inject" 2> "${result_prefix}.render.err"; then
            printf 'card prompt render failed (rc=%s): %s\n' "$?" \
                "$(tr '\n' ' ' < "${result_prefix}.render.err")" \
                > "${result_prefix}.missing"
            release_run_tree "$sandboxed" "$wt"
            return 0
        fi
        raw_prompt="$rendered"
    elif [ "$variant" = "real-saver" ]; then
        # Install the RTK hook into the scratch worktree's .claude/settings.json
        # via the dispatch helper itself. Failure here is "no silent fallback":
        # write a missing-reason marker into the result row, skip the claude
        # invocation, and let emit_table show the row as ?/?/—/?/? — the
        # operator can read <result_prefix>.missing for the reason.
        if ! apply_real_saver "$wt" > "${result_prefix}.rtk-bin" 2> "${result_prefix}.rtk-err"; then
            printf 'real-saver install failed (rc=%s): %s\n' "$?" \
                "$(tr '\n' ' ' < "${result_prefix}.rtk-err")" \
                > "${result_prefix}.missing"
            release_run_tree "$sandboxed" "$wt"
            return 0
        fi
    fi

    (
        cd "$wt"
        # Network kill for git. The card-shaped variants carry the REAL ship
        # instructions ("merge your branch into master and push"), and the
        # scratch worktree belongs to the real repo — an agent that reaches
        # the ship step would push a measurement artefact to origin/master.
        # `origin` is an ssh remote, so /bin/false as the ssh transport makes
        # every fetch/push fail immediately and locally. Applied to every
        # variant: none of them legitimately needs the network (the golden
        # task is local), it changes no prompt bytes, and keeping the two
        # arms in an identical environment is the point of the comparison.
        GIT_SSH_COMMAND=/bin/false GIT_TERMINAL_PROMPT=0 \
        timeout "$RUN_TIMEOUT_S" claude -p \
            --dangerously-skip-permissions \
            --output-format json \
            --model "${CLAUDE_MODEL:-sonnet}" \
            --strict-mcp-config --mcp-config "$empty_mcp" \
            < "$raw_prompt" > "${result_prefix}.json" \
            2> "${result_prefix}.err"
    )
    local run_rc=$?
    echo "$run_rc" > "${result_prefix}.exit"
    # A run killed by `timeout` is NOT a measurement. Its usage counters stop
    # wherever the kill landed, so publishing them next to a run that finished
    # on its own compares a partial transcript with a complete one — the exact
    # "numbers that don't describe what you think they describe" failure this
    # variant pair was added to fix (kaart 5934b954…). Record the reason and
    # emit no usage and no score, so the row renders as `?` with the reason
    # printed underneath instead of as a plausible-looking data point. The raw
    # .json stays on disk for anyone who wants to inspect the partial run.
    if [ "$run_rc" -eq 124 ]; then
        printf 'run hit the %ss timeout — incomplete, usage and score withheld (raise MEASURE_TIMEOUT_S)\n' \
            "$RUN_TIMEOUT_S" > "${result_prefix}.missing"
        release_run_tree "$sandboxed" "$wt"
        return 0
    fi
    if [ -s "${result_prefix}.json" ]; then
        parse_usage "${result_prefix}.json" > "${result_prefix}.usage" \
            2> "${result_prefix}.usage.err" || true
    fi
    BACKEND_DIR="$backend_dir" \
        score_golden "$wt" > "${result_prefix}.score" \
        2> "${result_prefix}.score.err" || true

    release_run_tree "$sandboxed" "$wt"
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
    local trial="$1" a="${2:-baseline}" b="${3:-with-saver}"
    local left="$RESULT_DIR/trial-${trial}-${a}.usage"
    local right="$RESULT_DIR/trial-${trial}-${b}.usage"
    if [ -s "$left" ] && [ -s "$right" ]; then
        printf '| %-18s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
            "trial-${trial}-delta" \
            "$(( $(sed -n 1p "$right") - $(sed -n 1p "$left") ))" \
            "$(( $(sed -n 2p "$right") - $(sed -n 2p "$left") ))" \
            "$(( $(sed -n 3p "$right") - $(sed -n 3p "$left") ))" \
            "$(( $(sed -n 4p "$right") - $(sed -n 4p "$left") ))" \
            "—" "—"
    fi
}

emit_table() {
    printf '| %-18s | %12s | %16s | %12s | %8s | %11s | %9s |\n' \
        label input cache_creation cache_read output pass_tests pass_diff
    printf '|--------------------|--------------|------------------|--------------|----------|-------------|------------|\n'
    local trial variant
    for trial in "$@"; do
        for variant in baseline with-saver card-baseline card-injector real-saver; do
            if [ -s "$RESULT_DIR/trial-${trial}-${variant}.json" ] \
                || [ -s "$RESULT_DIR/trial-${trial}-${variant}.score" ] \
                || [ -s "$RESULT_DIR/trial-${trial}-${variant}.missing" ]; then
                emit_row "trial-${trial}-${variant}"
                # If the row is missing (real-saver install failed), surface
                # the reason right under the row so operators don't have to
                # dig into the artifact directory to understand the "?".
                local missing_file="$RESULT_DIR/trial-${trial}-${variant}.missing"
                if [ -s "$missing_file" ]; then
                    printf '| %-18s | %s\n' "(reason)" "$(cat "$missing_file")"
                fi
            fi
        done
        if [ "$trial" -gt 0 ]; then
            emit_delta "$trial" "${DELTA_A:-baseline}" "${DELTA_B:-with-saver}"
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
    card-baseline)
        run_one 1 card-baseline
        emit_table 1
        ;;
    card-injector)
        run_one 1 card-injector
        emit_table 1
        ;;
    real-saver)
        run_one 1 real-saver
        emit_table 1
        ;;
    compare)
        run_one 1 baseline
        run_one 1 with-saver
        run_one 1 real-saver
        run_one 2 real-saver
        run_one 2 with-saver
        run_one 2 baseline
        emit_table 1 2
        ;;
    injector-compare)
        # Kaart 5934b954… — the canonical verbatim-slice measurement. Both
        # arms are rendered by the production build_card_prompt, so the only
        # byte difference is the injector kwargs and cache_read is measured
        # on the real dispatch prefix. Trial 1 forward, trial 2 reverse, so
        # each arm gets one cold-cache and one warm-cache position and
        # neither worktree state nor variant order is a confounder.
        run_one 1 card-baseline
        run_one 1 card-injector
        run_one 2 card-injector
        run_one 2 card-baseline
        DELTA_A=card-baseline DELTA_B=card-injector emit_table 1 2
        ;;
esac

# Print a discoverability footer so artifacts are findable.
printf '\n# artifacts: %s\n' "$RESULT_DIR"
printf '# files: %s\n' "$(ls -1 "$RESULT_DIR" 2>/dev/null | wc -l | tr -d ' ')"
