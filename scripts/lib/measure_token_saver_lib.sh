#!/usr/bin/env bash
# Pure helpers for scripts/measure-token-saver.sh.
#
# No side effects on import (no traps, no globals beyond the helpers). Source
# this file from the harness and from the test harness. Each helper is
# independently unit-testable.
#
# Helpers:
#   apply_saver <in> <out>          — byte-stable prompt mutation (RTK + Caveman + Ponytail)
#   render_card_prompt <in> <out> <0|1> — production build_card_prompt, injectors off/on
#   parse_usage <json>              — emits input / cache_creation / cache_read / output on 4 lines
#   score_golden <worktree>         — emits "pass_tests=<0|1>" on 1 line
#   build_prompt <worktree>         — emits the deterministic golden-task prompt on stdout
#   resolve_measurement_base_ref    — origin/master → master → HEAD
#   prepare_golden_revert <worktree> — creates the broken fixture or fails closed
#   make_worktree <repo> <path>     — echo the new worktree path on stdout
#   cleanup_worktree <repo> <path>  — git worktree remove --force + prune

# The one global this file defines on import: the absolute directory of the
# lib itself, captured at source time. `${BASH_SOURCE[0]}` is only reliable at
# file scope (inside a function it is empty under a non-bash caller), and
# `render_card_prompt`/`apply_real_saver` need the repo root to import the
# production backend. Deliberately NOT `$SCRIPT_DIR` — that global belongs to
# the caller and points at `scripts/`, one level up, which is why the earlier
# `$SCRIPT_DIR/../..` in this file resolved one directory above the repo root.
MEASURE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

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
# "pass_tests=<0|1>" on stdout.
#
# Honors PYTEST_CMD (from resolve-pytest-cmd.sh or override) and BACKEND_DIR.
# pass_tests: PYTEST_CMD exit code 0 on the zero_column_cap selector.
#             Scoped to tests/test_kanban_dispatch.py because the master
#             suite has collection errors in unrelated modules that import
#             `app.api.v1.agent_activity` (which doesn't exist on master);
#             a bare -k zero_column_cap across the whole suite exits
#             non-zero on those import errors even when the targeted two
#             tests pass.
#
# Earlier revisions also emitted a `pass_diff` column that grep-checked
# for the canonical `r.max_sessions >= 0` substring, but that scored 0
# in every run: agents rewrite the line to a functionally-equivalent form
# (e.g. `if r.max_sessions is not None`) that doesn't carry the
# canonical token. `pass_tests` is the meaningful quality signal here —
# it runs the two failing tests directly, so any rewrite that satisfies
# them passes. Removed in kaart 0a3ee4c9…; see also
# docs/cockpit/prompt-injectors-decision.md for the measurement context.
score_golden() {
    local wt="$1"
    local backend_dir="${BACKEND_DIR:-$wt/backend}"
    local pytest_cmd="${PYTEST_CMD:-}"

    if [ -z "$pytest_cmd" ] && [ -x "$backend_dir/venv/bin/pytest" ]; then
        pytest_cmd="$backend_dir/venv/bin/pytest"
    elif [ -z "$pytest_cmd" ] && command -v pytest >/dev/null 2>&1; then
        pytest_cmd="$(command -v pytest)"
    fi

    local pass_tests=0

    if [ -n "$pytest_cmd" ]; then
        if ( cd "$backend_dir" && "$pytest_cmd" tests/test_kanban_dispatch.py -k zero_column_cap -q >/dev/null 2>&1 ); then
            pass_tests=1
        fi
    fi

    echo "pass_tests=$pass_tests"
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

# --- render_card_prompt --------------------------------------------------
# Render a task body into a full dispatch prompt by calling the PRODUCTION
# assembler, ``backend/app/kanban/dispatch.py::build_card_prompt`` — the same
# function ``_run_card`` calls on every real spawn. The third argument decides
# whether the two injector kwargs carry the verbatim Caveman + Ponytail slices
# (``1``) or the empty strings a lane with both flags off produces (``0``).
#
#     render_card_prompt <task-body-file> <out-file> <0|1>
#
# Why this replaced the earlier hand-assembled ``apply_injector`` (kaart
# 5934b954…, impediment ronde 2): that helper re-implemented the preamble by
# hand and got the order wrong — it put Ponytail *after* the task body, while
# production puts BOTH slices in the preamble, ahead of the card text
# (dispatch.py: ``preamble + "\n\n---\n\n" + "\n\n---\n\n".join(blocks) + "\n\n"``,
# then the card body, then the ship instructions). ``cache_read`` is a prefix
# property, so a wrong prefix measures the wrong thing. Calling the real
# function removes the whole class of drift: there is no second copy of the
# assembly order to keep in sync.
#
# Calling ``build_card_prompt`` directly is safe from a plain script — it
# reads only ``id``/``title``/``description`` off the card object and touches
# no DB (the same reason ``backend/tests/test_prompt_injectors.py`` drives it
# with a ``SimpleNamespace``). The persona comes from the production reader
# (``_read_persona``) so the measured prefix is the real engineer-lane prefix,
# not a stand-in.
#
# Both arms of the measurement go through this one function, so the ONLY
# byte-level difference between them is the injector slices themselves.
#
# Fails closed (non-zero) when the production import cannot resolve — a
# silent fallback would emit numbers that don't describe the dispatch shape,
# which is exactly the bug this helper exists to fix.
render_card_prompt() {
    local in="$1" out="$2" inject="${3:-0}"
    # Resolve the repo root from this file's own path — not from a
    # caller-defined $SCRIPT_DIR. scripts/lib/ → ../.. is the repo root.
    local repo_root
    repo_root="$(cd "$MEASURE_LIB_DIR/../.." 2>/dev/null && pwd)"
    if [ -z "$repo_root" ] || [ ! -d "$repo_root/backend/app/kanban" ]; then
        echo "error: cannot resolve repo root with backend/app/kanban from $MEASURE_LIB_DIR" >&2
        return 13
    fi
    PYTHONPATH="${PYTHONPATH:-}:$repo_root/backend" \
    REPO_ROOT_FOR_PERSONA="$repo_root" \
    python3 - "$in" "$out" "$inject" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["REPO_ROOT_FOR_PERSONA"], "backend"))
try:
    from app.kanban.dispatch import build_card_prompt, _read_persona
    from app.kanban.prompt_injectors import CAVEMAN_PROMPT, PONYTAIL_PROMPT
except Exception as exc:
    print(
        "error: cannot import production prompt assembler from app.kanban.dispatch: "
        f"{type(exc).__name__}: {exc}",
        file=sys.stderr,
    )
    sys.exit(14)
from types import SimpleNamespace

inject = sys.argv[3] == "1"
body = open(sys.argv[1]).read()
card = SimpleNamespace(
    id="measure-golden-task",
    title="Golden task: re-apply the zero-column-cap dispatch fix",
    description=body,
)
prompt = build_card_prompt(
    card,
    persona=_read_persona(os.environ["REPO_ROOT_FOR_PERSONA"], "engineer"),
    ship_mode="direct",
    prompt_injector_caveman=CAVEMAN_PROMPT if inject else "",
    prompt_injector_ponytail=PONYTAIL_PROMPT if inject else "",
)
open(sys.argv[2], "w").write(prompt)
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


# --- isolate_kanban_writes -----------------------------------------------
# Reject outbound traffic to 127.0.0.1:8000 and [::1]:8000 from this host
# so a measured agent that follows the dispatch prompt's REST fallback
# cannot reach the live kanban board.
#
# Why this is its own primitive (kaart ee905064… board-side, follow-up on
# commit 4af88105). The git sandbox (`make_prompt_sandbox`) closes the
# ship-recipe path; the dispatch prompt at backend/app/kanban/dispatch.py
# line 2513 also instructs the agent to "go straight to REST for every
# subsequent board update" against `http://localhost:8000/api/v1/kanban`
# when MCP is pinned empty — which the harness does on purpose. The board
# is therefore a second mutation surface that the git sandbox does not
# touch. The human-decided answer (kaart ee905064… impediment, 2026-08-10)
# was to close this write path structurally rather than rely on prompt
# wording.
#
# Why nftables and not an env var (kaart 5934b954…, lesson carried into
# §3.4 of harnas-spawn-inventaris.md). `GIT_SSH_COMMAND=/bin/false` did
# not survive into the shell the agent ran git from; an `HTTP_PROXY=`
# pointing at a sink has the same problem, since both are read by the
# child process spawned by claude's tool framework. nftables operates at
# the kernel level — the OUTPUT hook fires on every packet from every
# socket in this process tree, regardless of which subshell or library
# the agent used to open it (curl, urllib, raw socket). Priority -150
# keeps our chain ahead of WSL2's `WSLOUTPUT` (priority filter = 0), so
# the rejection happens before WSL's mark-and-masquerade runs.
#
# sudo is pre-authorized in this environment (Cockpit dev box, see
# `docs/cockpit/isolated-component-preview.md`); sudo -n refuses fast
# when no cached credentials exist. The helper fails closed (non-zero)
# when it cannot install the rule — the harness turns that into a
# `.missing` row instead of running the agent without containment.
#
# The table name defaults to a unique-per-source value (`_ISOLATION_TABLE`
# below), so two harness instances running concurrently each install under
# their own slot and never clobber each other. `release_kanban_isolation`
# only deletes the slot it knows about — operators can `sudo nft delete
# table inet measure_kanban_isolation_<slot>` to clean up crashed
# predecessors manually. The optional first argument lets tests pin a
# specific table name (otherwise `(cmd) &` subshells all see `$$` as the
# same parent PID).
#
# Known limitations, documented in §2.6:
#   1. The rule is host-wide (within the operator's UID). Cockpit UI and
#      other agents by the same user temporarily lose :8000 reachability
#      during a run. Per-process-tree scoping requires a network namespace
#      with veth-paar + NAT (CAP_SYS_ADMIN), which this environment
#      cannot provide; the alternative is a separate user+net namespace
#      via `unshare -Urn` (filed as open point in §7).
#   2. An adversarial agent can `sudo nft delete table inet ...` because
#      the operator's sudoers covers it. The rule closes the *accidental*
#      write path (a measured agent that follows the prompt without
#      thinking), not the adversarial one.
#   3. The signal trap releases the rule on EXIT/INT/TERM but does not
#      kill the `claude` child — if the harness is killed mid-run and
#      the child continues, it regains :8000 reachability for whatever
#      lifetime it has left. Tracking the child PID across a subshell
#      with `timeout` requires restructuring `run_one`; not in scope here.
isolate_kanban_writes() {
    local table="${1:-$_ISOLATION_TABLE}"

    # Clear any stale table from a previous (possibly crashed) run with
    # this slot. nft `delete table` returns non-zero when the table is
    # absent; ignore.
    sudo -n nft delete table "inet $table" >/dev/null 2>&1 || true

    # Install the rules. Four nft calls: table, chain, IPv4 rule, IPv6
    # rule. The IPv6 rule closes the bypass where `localhost` resolves to
    # `[::1]` (modern glibc prefers IPv6 when both are reachable). Each
    # call can fail independently on permission or syntax errors. Treat
    # any failure as fatal — partial state is worse than no state (the
    # agent might think the rule is active when the OUTPUT chain is empty).
    if sudo -n nft add table "inet $table" 2>"${ISOLATION_ERR:-/dev/null}" \
        && sudo -n nft "add chain inet $table output { type filter hook output priority -150 ; policy accept ; }" 2>>"${ISOLATION_ERR:-/dev/null}" \
        && sudo -n nft add rule "inet $table" output ip daddr 127.0.0.1 tcp dport 8000 reject 2>>"${ISOLATION_ERR:-/dev/null}" \
        && sudo -n nft add rule "inet $table" output ip6 daddr ::1 tcp dport 8000 reject 2>>"${ISOLATION_ERR:-/dev/null}"; then
        _ISOLATION_INSTALLED=1
        return 0
    fi
    _ISOLATION_INSTALLED=0
    return 1
}

# --- release_kanban_isolation -------------------------------------------
# Idempotent: deletes the per-slot table whether this process installed
# it or a stale predecessor did. Accepts an optional table-name override
# (mirrors `isolate_kanban_writes`).
release_kanban_isolation() {
    local table="${1:-$_ISOLATION_TABLE}"
    sudo -n nft delete table "inet $table" >/dev/null 2>&1 || true
    _ISOLATION_INSTALLED=0
}

# --- probe_kanban_isolated ----------------------------------------------
# Return 0 when 127.0.0.1:8000 is currently unreachable from this process,
# 1 when a connection succeeds. Used by tests and by the harness's
# fail-closed sanity check (kaart ee905064… §2.6). /dev/tcp is a bash
# built-in that opens a TCP connection or fails fast — no nc / curl needed.
probe_kanban_isolated() {
    timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/8000' >/dev/null 2>&1
}

# Per-source state for isolate_kanban_writes / release_kanban_isolation.
# Set on import; reset by the helpers themselves. The default table name
# is sourced once at import time. Distinct bash processes get distinct
# `$$` values, so two harnesses on the same box do not clobber each
# other; a single bash process that spawns multiple subshells sees the
# same `$$` in all of them (a known bash limitation) — such callers
# must pass an explicit table-name argument to the helpers.
_ISOLATION_INSTALLED=0
_ISOLATION_TABLE="measure_kanban_isolation_$$"

# --- make_prompt_sandbox -------------------------------------------------
# Materialise <ref>'s tree into <dest> as PLAIN FILES — no .git, no remotes,
# no credentials, and outside every git working tree.
#
#     make_prompt_sandbox <repo> <ref> <dest>
#
# Why this exists (kaart 5934b954…, incident 2026-08-10). The card-shaped
# variants feed the agent the real dispatch prompt, ship recipe included. An
# agent measured that way follows it: one run reached the ship step, merged
# its golden-task edit and pushed it to origin/master — unreviewed production
# dispatch code authored by a measurement fixture (reverted in 2e0eb256).
#
# Two guards were tried and are NOT sufficient on their own:
#   * `GIT_SSH_COMMAND=/bin/false` on the claude invocation — the variable did
#     not survive into the shell the agent actually runs git from, and the
#     push went through anyway. Do not rely on env-level transport blocking.
#   * A scratch git worktree — `with_scratch_worktree` puts the checkout
#     INSIDE the repo and a linked worktree shares the parent's config, so it
#     shares the parent's remotes and credentials by construction.
#
# A `git archive` export has neither. The agent can still `git init` locally;
# it has nothing to push to. Keep the destination outside $REPO_ROOT so a
# stray `git` call cannot walk up into the real repository either.
make_prompt_sandbox() {
    local repo="$1" ref="$2" dest="$3"
    mkdir -p "$dest" || return 1
    command git -C "$repo" archive --format=tar "$ref" 2>/dev/null | tar -x -C "$dest" || return 1
    [ -f "$dest/backend/app/kanban/dispatch.py" ] || {
        echo "error: sandbox export from $ref is missing backend/app/kanban/dispatch.py" >&2
        return 1
    }
    [ ! -e "$dest/.git" ] || {
        echo "error: sandbox at $dest unexpectedly contains a .git entry" >&2
        return 1
    }
    return 0
}

# --- cleanup_prompt_sandbox ----------------------------------------------
cleanup_prompt_sandbox() {
    local dest="$1"
    case "$dest" in
        "$HOME"/.cache/*) rm -rf "$dest" ;;
        *) echo "refusing to remove sandbox outside \$HOME/.cache: $dest" >&2; return 1 ;;
    esac
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