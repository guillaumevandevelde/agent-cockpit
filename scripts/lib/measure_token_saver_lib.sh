#!/usr/bin/env bash
# Pure helpers for scripts/measure-token-saver.sh.
#
# No side effects on import (no traps, no globals beyond the helpers). Source
# this file from the harness and from the test harness. Each helper is
# independently unit-testable.
#
# Helpers:
#   apply_saver <in> <out>          — byte-stable prompt mutation (RTK + Caveman + Ponytail)
#   apply_injector <in> <out>       — verbatim Caveman + Ponytail slice from production constants
#   parse_usage <json>              — emits input / cache_creation / cache_read / output on 4 lines
#   score_golden <worktree>         — emits "pass_tests=<0|1>\npass_diff=<0|1>" on 2 lines
#   build_prompt <worktree>         — emits the deterministic golden-task prompt on stdout
#   resolve_measurement_base_ref    — origin/master → master → HEAD
#   prepare_golden_revert <worktree> — creates the broken fixture or fails closed
#   make_worktree <repo> <path>     — echo the new worktree path on stdout
#   cleanup_worktree <repo> <path>  — git worktree remove --force + prune

# --- apply_saver ---------------------------------------------------------
# Pure, deterministic, byte-stable. Two runs on identical input → identical
# SHA-256. The proxy is a documented lower bound on a real RTK/Caveman/
# Ponytail pipeline (see docs/cockpit/token-saver-meet-harnas.md §3).
apply_saver() {
    local in="$1" out="$2"
    python3 - "$in" "$out" <<'PY'
import re, pathlib, sys
src = pathlib.Path(sys.argv[1]).read_text()
CAVEMAN_PRELUDE = (
    "[SAVER:CAVEMAN] Respond with the shortest possible phrasing. "
    "Drop politeness, hedges, and restatements.\n\n"
)
PONYTAIL_TAIL = (
    "\n\n[SAVER:PONYTAIL] Prefer one Bash call over many. "
    "No code fences unless asked."
)
mutated = CAVEMAN_PRELUDE + src + PONYTAIL_TAIL
# Collapse runs of 3+ newlines to exactly 2.
mutated = re.sub(r'\n{3,}', '\n\n', mutated)
# Dedup identical +/- lines in diff hunks (two consecutive identical lines
# collapse to one). Multiline; treats each line independently.
mutated = re.sub(r'^([+-])([^\n]*)\n\1\2$', r'\1\2', mutated, flags=re.M)
pathlib.Path(sys.argv[2]).write_text(mutated)
PY
}

# --- parse_usage ---------------------------------------------------------
# Reads a `claude -p --output-format json` response. Emits four integers on
# four lines, in order: input_tokens / cache_creation_input_tokens /
# cache_read_input_tokens / output_tokens. Missing fields default to 0.
# Unparseable JSON → PARSE_ERROR to stderr, exit 2.
parse_usage() {
    local json="$1"
    python3 - "$json" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        payload = json.load(fh)
except Exception as exc:
    print(f"PARSE_ERROR:{type(exc).__name__}:{exc}", file=sys.stderr)
    sys.exit(2)
u = payload.get("usage", {}) if isinstance(payload, dict) else {}
print(u.get("input_tokens", 0))
print(u.get("cache_creation_input_tokens", 0))
print(u.get("cache_read_input_tokens", 0))
print(u.get("output_tokens", 0))
PY
}

# --- build_prompt --------------------------------------------------------
# Build the deterministic golden-task prompt. Reads the (post-revert)
# backend/app/kanban/dispatch.py + the two failing test names, and emits a
# prompt that asks the agent to satisfy them.
build_prompt() {
    local wt="$1"
    cat <<EOF
You are working in a temporary git worktree at $wt.

The repository has a regression that was fixed in commit b30a9bb ("fix(kanban):
pause columns with zero session cap"). That fix has been reverted here so the
test suite is failing. Your job: re-apply the fix.

Two tests are failing in backend/tests/test_kanban_dispatch.py:

- test_zero_column_cap_blocks_dispatch
- test_zero_column_cap_does_not_block_other_columns

The fix lives in backend/app/kanban/dispatch.py in the function
_column_max_sessions. The current (broken) line is:

    return {r.name: r.max_sessions for r in rows if r.max_sessions is not None and r.max_sessions > 0}

Read the two failing tests to understand the expected behaviour, then apply
the smallest possible change to backend/app/kanban/dispatch.py that makes
both tests pass. Do not edit the test file. Do not touch any other file.

When done, run pytest -k zero_column_cap from backend/ to confirm, and report
the one-line diff you produced.
EOF
}

# --- score_golden --------------------------------------------------------
# Score a worktree against the golden-task acceptance criteria. Emits
# "pass_tests=<0|1>\npass_diff=<0|1>" on stdout.
#
# Honors PYTEST_CMD (from resolve-pytest-cmd.sh or override) and BACKEND_DIR.
# pass_tests: PYTEST_CMD exit code 0 on the zero_column_cap selector.
#             Scoped to tests/test_kanban_dispatch.py because the master
#             suite has collection errors in unrelated modules that import
#             `app.api.v1.agent_activity` (which doesn't exist on master);
#             a bare -k zero_column_cap across the whole suite exits
#             non-zero on those import errors even when the targeted two
#             tests pass.
# pass_diff:  git diff -- backend/app/kanban/dispatch.py matches the canonical
#             single-line `>` → `>=` revert (line-level substring check).
score_golden() {
    local wt="$1"
    local backend_dir="${BACKEND_DIR:-$wt/backend}"
    local pytest_cmd="${PYTEST_CMD:-}"

    if [ -z "$pytest_cmd" ] && [ -x "$backend_dir/venv/bin/pytest" ]; then
        pytest_cmd="$backend_dir/venv/bin/pytest"
    elif [ -z "$pytest_cmd" ] && command -v pytest >/dev/null 2>&1; then
        pytest_cmd="$(command -v pytest)"
    fi

    local pass_tests=0 pass_diff=0

    if [ -n "$pytest_cmd" ]; then
        if ( cd "$backend_dir" && "$pytest_cmd" tests/test_kanban_dispatch.py -k zero_column_cap -q >/dev/null 2>&1 ); then
            pass_tests=1
        fi
    fi

    # pass_diff: in the working tree's *committed* (HEAD) state, the broken
    # line must be absent and the fixed line must be present. We score the
    # working tree (not the diff) so "added fix AND removed broken" and
    # "untouched file" don't both score 1.
    local dispatch_py="$wt/backend/app/kanban/dispatch.py"
    if [ -f "$dispatch_py" ] \
       && ! grep -q 'r.max_sessions > 0' "$dispatch_py" \
       && grep -q 'r.max_sessions >= 0' "$dispatch_py"; then
        pass_diff=1
    fi

    echo "pass_tests=$pass_tests"
    echo "pass_diff=$pass_diff"
}

# --- resolve_measurement_base_ref -----------------------------------------
# Preserve the harness's original stable-baseline order even when it is run
# from a feature worktree.
resolve_measurement_base_ref() {
    local repo="$1"
    if command git -C "$repo" rev-parse --verify origin/master >/dev/null 2>&1; then
        echo "origin/master"
    elif command git -C "$repo" rev-parse --verify master >/dev/null 2>&1; then
        echo "master"
    else
        echo "HEAD"
    fi
}

# --- prepare_golden_revert ------------------------------------------------
# Put the fixture in the known-broken state. Refuse to continue when the
# selected baseline does not contain the expected fixed line: sed otherwise
# succeeds without changing anything and the harness reports plausible but
# meaningless measurements.
prepare_golden_revert() {
    local wt="$1"
    local dispatch_py="$wt/backend/app/kanban/dispatch.py"
    local fixed='r.max_sessions >= 0'
    local broken='r.max_sessions > 0'

    if [ ! -f "$dispatch_py" ] || ! grep -qF "$fixed" "$dispatch_py"; then
        echo "error: golden task expected fixed line '$fixed' in $dispatch_py" >&2
        return 1
    fi

    sed -i "s/$fixed/$broken/" "$dispatch_py"
}

# --- apply_injector ------------------------------------------------------
# Apply the verbatim upstream Caveman + Ponytail prompt slices to a prompt
# file, mirroring the production preamble assembly in
# ``backend/app/kanban/dispatch.py::build_card_prompt``. The slices come
# straight from ``backend/app/kanban/prompt_injectors.py`` (the same
# constants the resolver passes to the dispatch kwargs) so this is a true
# verbatim measurement — NOT the ~110-byte stub of ``apply_saver``.
#
# Layout produced (separators match the production code's
# ``preamble + "\n\n---\n\n" + ... + "\n\n"`` pattern):
#
#     <CAVEMAN_PROMPT.rstrip()>
#     <blank>
#     ---
#     <blank>
#     <original prompt body>
#     <blank>
#     ---
#     <blank>
#     <PONYTAIL_PROMPT.rstrip()>
#
# Mirrors what an injected dispatch sees when both column flags are on
# (the acceptatiecriterium the kaart noemde: "kwargs op build_card_prompt
# met en zonder injectie"). The harness can't reach ``build_card_prompt``
# directly because the path goes through ``_run_card`` and a real
# kanban-DB; this helper stands in for that path with the same bytes.
#
# Fails closed (non-zero) when the import cannot resolve the production
# module — a silent fallback would emit a measurement that isn't the
# verbatim slice and is exactly the bug this helper exists to fix.
apply_injector() {
    local in="$1" out="$2"
    # Resolve the repo root from BASH_SOURCE so the inline Python can import
    # backend.app.kanban.prompt_injectors. SCRIPT_DIR (defined at the top
    # of this file as the directory of measure_token_saver_lib.sh) points at
    # scripts/lib/; its grandparent is the repo root. We pass that on
    # PYTHONPATH and additionally insert ./backend on sys.path inside the
    # Python heredoc, so the import resolves no matter where the caller
    # invoked from (the production run from the harness has cwd=repo root;
    # tests may run from a synthetic tmp dir as long as that dir also has
    # a backend/ tree — see test_measure_token_saver.sh).
    local repo_root
    repo_root="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)"
    if [ -z "$repo_root" ]; then
        echo "error: cannot resolve repo root from $SCRIPT_DIR" >&2
        return 13
    fi
    PYTHONPATH="${PYTHONPATH:-}:$repo_root" \
    python3 - "$in" "$out" <<'PY'
import sys
sys.path.insert(0, "backend")
try:
    from app.kanban.prompt_injectors import CAVEMAN_PROMPT, PONYTAIL_PROMPT
except Exception as exc:
    print(f"error: cannot import verbatim slice from app.kanban.prompt_injectors: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(14)
src = open(sys.argv[1]).read()
out = CAVEMAN_PROMPT.rstrip() + "\n\n---\n\n" + src + "\n\n---\n\n" + PONYTAIL_PROMPT.rstrip() + "\n"
open(sys.argv[2], "w").write(out)
PY
}


# --- apply_real_saver ----------------------------------------------------
# Install the RTK token-saver hook into a scratch worktree. Reuses the
# exact same JSON-merge code path the dispatch helper uses, so the
# measurement covers the production code path. Refuses to fall back to
# a prompt-mutation proxy: a real-saver run that can't install RTK
# fails closed and the result is reported as missing rather than as
# a quiet proxy measurement.
#
# Resolution order for the RTK binary (mirrors the dispatch helper):
#   1. $COCKPIT_RTK_BIN env override (operator ad-hoc testing)
#   2. The cache directory under COCKPIT_RTK_CACHE_ROOT (default
#      $HOME/.local/share/cockpit/rtk/<version>/bin/rtk)
#   3. PATH (shutil.which("rtk"))
# Emits the absolute RTK binary path on stdout, or returns non-zero
# when no binary resolves. The exit code carries the missing-binary
# reason so the harness can write it into the result row.
apply_real_saver() {
    local wt="$1"
    local version="${RTK_PINNED_VERSION:-0.43.0}"
    local cache_root="${COCKPIT_RTK_CACHE_ROOT:-$HOME/.local/share/cockpit/rtk}"
    local binary

    if [ -n "${COCKPIT_RTK_BIN:-}" ] && [ -x "$COCKPIT_RTK_BIN" ]; then
        binary="$COCKPIT_RTK_BIN"
    elif [ -x "$cache_root/$version/bin/rtk" ]; then
        binary="$cache_root/$version/bin/rtk"
    elif command -v rtk >/dev/null 2>&1; then
        binary="$(command -v rtk)"
    else
        echo "error: rtk binary not found (COCKPIT_RTK_BIN, cache, and PATH all empty)" >&2
        return 11
    fi

    # Refuse if the binary is the wrong version — "no silent fallback"
    # contract (token-saver-mechanismen-decision.md §8). Anything other
    # than "<version>[.x]" → refuse.
    local reported
    reported="$("$binary" --version 2>/dev/null | head -n1 || true)"
    if ! printf '%s' "$reported" | grep -Eq "^rtk ${version}(\\.[0-9]+)?$"; then
        echo "error: rtk version mismatch (got '$reported', need ${version}.x)" >&2
        return 12
    fi

    # Delegate the JSON-merge + wrapper install to the dispatch helper
    # itself. ``-c`` runs the same code path ``make_worktree_transport``
    # runs on every spawn (kaart c31333bf…). This is the explicit
    # lockstep the design spec §8.4 calls for.
    PYTHONPATH="${PYTHONPATH:-}:$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd)" \
    python3 - "$wt" "$binary" <<'PY'
import sys
sys.path.insert(0, "backend")
from app.kanban.token_saver import write_rtk_settings_into_worktree
write_rtk_settings_into_worktree(sys.argv[1], sys.argv[2])
print(sys.argv[2])
PY
}


# --- make_worktree -------------------------------------------------------
# Create a detached scratch worktree. Tries origin/master first, then
# master, then HEAD. Echoes the new worktree path on stdout. Returns
# non-zero if no usable ref exists. Caller's responsibility to invoke
# cleanup_worktree on EXIT.
make_worktree() {
    local repo="$1" path="$2" source_ref
    source_ref="$(resolve_measurement_base_ref "$repo")"
    # Use `command git` so shell aliases do not intercept this call. Redirect
    # progress output so it cannot leak into the caller's stdout capture.
    command git -C "$repo" worktree add --detach "$path" "$source_ref" >/dev/null 2>&1
    echo "$path"
}

# --- cleanup_worktree ----------------------------------------------------
# Remove a scratch worktree and prune its gitdir entry.
cleanup_worktree() {
    local repo="$1" path="$2"
    if [ -n "${path:-}" ] && [ -d "$path" ]; then
        command git -C "$repo" worktree remove --force "$path" >/dev/null 2>&1 || true
    fi
    command git -C "$repo" worktree prune >/dev/null 2>&1 || true
}