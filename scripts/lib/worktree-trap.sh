#!/usr/bin/env bash
# Pure helpers for harness scripts that need a scratch git worktree.
#
# Why this file exists
# --------------------
# The naive `WT="$(mktemp -d -p "$REPO_ROOT")/wt-$$"` + `trap 'cleanup ...'
# EXIT` pattern leaves the `mktemp -d` parent directory behind in the
# repo working tree. The parent is a `mktemp`-named hidden directory
# (e.g. `.tmp.abc123/`) that's invisible to plain `ls`, so each
# iteration of a harness silently accumulates them. Cards
# `5c508644…` (mktemp-d cleanup trap) and `513e37a1…` (near-clobber
# from a write that landed on the main checkout) both came out of
# this trap.
#
# What this file provides
# -----------------------
#   with_scratch_worktree  <repo_root> <var_name> [source_ref]
#       Creates a scratch worktree at
#         <repo_root>/.tmp-<uniq-id>/wt-<pid>
#       owned by this helper, installs an EXIT trap that removes
#       BOTH the `.tmp-<uniq-id>` parent AND the worktree on exit,
#       and binds the worktree path to <var_name> in the caller's
#       scope. Echoes the worktree path on stdout. source_ref defaults to
#       HEAD; callers that require a stable baseline must pass it explicitly.
#
#   cleanup_scratch_worktree <repo_root> <wt_path>
#       Same as the EXIT trap (idempotent). Caller can invoke it
#       explicitly when they want cleanup before the script ends.
#
# Pattern in a harness:
#
#     source "$SCRIPT_DIR/lib/worktree-trap.sh"
#     with_scratch_worktree "$REPO_ROOT" WT         # WT is the worktree path
#     # trap is installed automatically
#     do_stuff "$WT"
#
# Both the worktree AND the `.tmp-<uniq-id>` parent are removed on
# exit (success, error, or signal). Subsequent runs leave no
# `.tmp*` siblings under $REPO_ROOT.

# Under zsh, `trap ... EXIT` set inside a function is FUNCTION-SCOPED
# and fires the moment the function returns — not at shell exit. If we
# just install the trap as-is, the worktree is created and then
# immediately destroyed before the caller can use it, and the trap
# function never runs in the caller's scope. `setopt POSIX_TRAPS` makes
# zsh install the trap at the shell level (matching bash) and is the
# only portable-in-this-file fix that keeps callers' existing trap
# semantics (success, error, EXIT, signals) intact. We set it once at
# source time, which is a documented side-effect: callers sourcing this
# lib accept POSIX-trap semantics for the rest of the script.
if [ -n "${ZSH_VERSION:-}" ]; then
    setopt POSIX_TRAPS 2>/dev/null || true
fi

# Registry of every scratch worktree the current shell owns. The EXIT
# trap reads this once at shell exit and cleans them all. Without the
# registry, every call to with_scratch_worktree would overwrite the
# previous trap and leak the prior scratch — exactly the
# `tmp-<id>`-accumulation the card (and the earlier `5c508644…` card)
# tried to prevent. Format is a newline-separated list of `repo<TAB>path`
# pairs so a single string survives bash↔zsh round-trips without
# depending on the array semantics either shell provides differently.
__SCRATCH_WT_REGISTRY=""

# --- with_scratch_worktree ----------------------------------------------
# Create a scratch worktree + owned parent + cleanup trap in one go.
#
# Args:
#   $1 — repo root (where `git worktree` is anchored)
#   $2 — name of the variable in the caller's scope to bind the
#        resolved path to
#   $3 — optional git source ref (defaults to HEAD)
#
# IMPORTANT — must run in the caller's scope (no `$()`):
#   `WT=$(with_scratch_worktree "$repo" WT)` puts the helper in a
#   subshell, so the EXIT trap is captured and lost when the subshell
#   exits. Call it bare:
#
#       with_scratch_worktree "$repo" WT
#       # or use a temp file:
#       WT_PATH_FILE="$(mktemp)"
#       with_scratch_worktree "$repo" WT >"$WT_PATH_FILE"
#       WT="$(cat "$WT_PATH_FILE")"; rm -f "$WT_PATH_FILE"
#
# Returns non-zero if `git worktree add` fails — the cleanup trap is
# installed up-front so a failed install still removes whatever was
# created.
with_scratch_worktree() {
    local repo="$1" var="$2" source_ref="${3:-HEAD}"

    # Build the path under a mktemp parent INSIDE the repo root. We
    # use a non-leading-dot prefix (`tmp-`) so the parent is visible
    # to `ls` during debug — the original `.tmp.<id>` shape made
    # accumulation invisible, which is exactly what hid the bug from
    # users for so long. A leading-tilde `~`-style is unsafe in
    # shells; `tmp-` keeps it grep-friendly.
    local scratch_id wt_path parent_dir
    scratch_id="$(mktemp -u tmp-XXXXXXXX 2>/dev/null || echo "tmp-$$-$RANDOM")"
    parent_dir="$repo/$scratch_id"
    wt_path="$parent_dir/wt-$$"

    # Register the scratch BEFORE creating anything so a mid-create
    # failure still removes whatever did land on disk. The trap body
    # is a fixed string (no per-call interpolation) so it can be set
    # exactly once and re-set cheaply on every call without
    # accumulating args. The registry is the single source of truth.
    __SCRATCH_WT_REGISTRY="${__SCRATCH_WT_REGISTRY:+${__SCRATCH_WT_REGISTRY}
}${repo}	${wt_path}"
    trap '__scratch_worktree_trap_all' EXIT

    mkdir -p "$parent_dir"

    # `git worktree add` requires the target dir to NOT exist yet.
    # Create it as an empty dir and let git populate it.
    mkdir -p "$wt_path"

    local err
    if ! err="$(command git -C "$repo" worktree add --detach "$wt_path" "$source_ref" 2>&1)"; then
        echo "error: with_scratch_worktree: git worktree add failed at $wt_path: $err" >&2
        # Force cleanup so the failed parent doesn't linger.
        cleanup_scratch_worktree "$repo" "$wt_path"
        # Drop the failed entry from the registry so the global trap
        # does not double-remove it.
        __SCRATCH_WT_REGISTRY="$(printf '%s\n' "$__SCRATCH_WT_REGISTRY" | grep -vF "	${wt_path}"$'\t' | grep -vF "$(printf '%s\t%s' "$repo" "$wt_path")")"
        return 1
    fi

    # Publish path to caller via the named variable + stdout.
    printf -v "$var" '%s' "$wt_path"
    printf '%s\n' "$wt_path"
}

# --- cleanup_scratch_worktree -------------------------------------------
# Explicit/inline cleanup — removes the worktree AND its mktemp parent.
# Idempotent: missing dirs are not an error. The EXIT trap installed
# by with_scratch_worktree calls this internally.
cleanup_scratch_worktree() {
    # NOTE — never name a local `path` here (nor cdpath/fpath/manpath/argv).
    # Harnesses source this lib from the dispatch shell, which is zsh, and in
    # zsh `path` is a SPECIAL array parameter tied to $PATH. `local path="$2"`
    # therefore replaced PATH with the worktree path for the rest of this
    # function, and every external binary below died at once:
    #     cleanup_scratch_worktree:12: command not found: dirname
    # The trap then aborted mid-flight, leaving both the registered worktree
    # and the `tmp-<id>` parent behind — worse than the naive `mktemp -d`
    # pattern this helper exists to replace, because callers trust the trap.
    # Invisible in bash, where `path` is an ordinary scalar (card 95f5199c…).
    local repo="$1" wt_path="$2"
    if [ -n "${wt_path:-}" ] && [ -d "$wt_path" ]; then
        command git -C "$repo" worktree remove --force "$wt_path" >/dev/null 2>&1 || true
    fi
    command git -C "$repo" worktree prune >/dev/null 2>&1 || true

    if [ -z "${wt_path:-}" ]; then
        return 0
    fi

    # Pure parameter expansion instead of `dirname`/`basename`: the cleanup
    # path must not depend on PATH resolution at all, so a hostile or stripped
    # PATH in a teardown context can never strand the scratch dirs again.
    local parent_dir
    parent_dir="${wt_path%/*}"
    parent_dir="${parent_dir%/}"
    if [ -z "${parent_dir:-}" ] || [ "$parent_dir" = "$wt_path" ]; then
        return 0
    fi

    # Safety net: ONLY remove the parent if it matches our scratch
    # pattern (`<repo>/tmp-<id>/`). Never delete arbitrary external
    # parent dirs — that would wipe out callers' tmpdirs.
    local basename_parent
    basename_parent="${parent_dir##*/}"
    case "$basename_parent" in
        tmp-*) rm -rf "$parent_dir" 2>/dev/null || true ;;
        *)     ;;  # caller-owned parent — leave it alone
    esac

    # Drop the entry from the registry so an explicit cleanup does not
    # also fire a redundant global cleanup on EXIT.
    if [ -n "${__SCRATCH_WT_REGISTRY:-}" ]; then
        __SCRATCH_WT_REGISTRY="$(printf '%s\n' "$__SCRATCH_WT_REGISTRY" | grep -vF "$(printf '%s\t%s' "$repo" "$wt_path")")"
    fi
}

# Internal: single EXIT trap body. Reads the registry and cleans every
# `repo<TAB>wt_path` pair. Installed once and re-asserted on every
# with_scratch_worktree call (cheap — same string). `set +u` keeps the
# trap robust against callers that run with `set -u` (a missing
# registry would otherwise blow up here).
__scratch_worktree_trap_all() {
    set +u
    if [ -z "${__SCRATCH_WT_REGISTRY:-}" ]; then
        return 0
    fi
    # Snapshot the registry and clear it BEFORE iterating. If the
    # cleanup body itself traps (e.g. set -e on a caller-provided
    # function) we don't want a re-entry to see the same entries
    # twice. Same trick POSIX-aware shells use for signal handlers.
    local snapshot
    snapshot="$__SCRATCH_WT_REGISTRY"
    __SCRATCH_WT_REGISTRY=""
    local line repo wt_path
    while IFS= read -r line || [ -n "$line" ]; do
        [ -z "$line" ] && continue
        # Split on first tab only — wt_path is fully expanded so
        # cannot contain a tab. Use parameter expansion rather than
        # `read repo wt_path` so the field separator handling is
        # identical between bash and zsh.
        repo="${line%%	*}"
        wt_path="${line#*	}"
        [ -z "$wt_path" ] && wt_path="${line#*	}"  # no-op safety
        cleanup_scratch_worktree "$repo" "$wt_path"
    done <<EOF
$snapshot
EOF
}
