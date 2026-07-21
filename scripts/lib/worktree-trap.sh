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
#   with_scratch_worktree  <repo_root> <var_name>
#       Creates a scratch worktree at
#         <repo_root>/.tmp-<uniq-id>/wt-<pid>
#       owned by this helper, installs an EXIT trap that removes
#       BOTH the `.tmp-<uniq-id>` parent AND the worktree on exit,
#       and binds the worktree path to <var_name> in the caller's
#       scope. Echoes the worktree path on stdout.
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

# --- with_scratch_worktree ----------------------------------------------
# Create a scratch worktree + owned parent + cleanup trap in one go.
#
# Args:
#   $1 — repo root (where `git worktree` is anchored)
#   $2 — name of the variable in the caller's scope to bind the
#        resolved path to
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
    local repo="$1" var="$2"

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

    # Install the cleanup trap BEFORE creating anything so a mid-create
    # failure still removes whatever did land on disk. Interpolate the
    # path values at install time so the trap survives pos-arg
    # clobbering when the script continues running.
    trap "__scratch_worktree_trap '${repo}' '${wt_path}'" EXIT

    mkdir -p "$parent_dir"

    # `git worktree add` requires the target dir to NOT exist yet.
    # Create it as an empty dir and let git populate it.
    mkdir -p "$wt_path"

    local err
    if ! err="$(command git -C "$repo" worktree add --detach "$wt_path" HEAD 2>&1)"; then
        echo "error: with_scratch_worktree: git worktree add failed at $wt_path: $err" >&2
        # Force cleanup so the failed parent doesn't linger.
        cleanup_scratch_worktree "$repo" "$wt_path"
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
    local repo="$1" path="$2"
    if [ -n "${path:-}" ] && [ -d "$path" ]; then
        command git -C "$repo" worktree remove --force "$path" >/dev/null 2>&1 || true
    fi
    command git -C "$repo" worktree prune >/dev/null 2>&1 || true

    if [ -z "${path:-}" ]; then
        return 0
    fi

    local parent_dir
    parent_dir="$(dirname "${path}")"
    parent_dir="${parent_dir%/}"
    if [ -z "${parent_dir:-}" ]; then
        return 0
    fi

    # Safety net: ONLY remove the parent if it matches our scratch
    # pattern (`<repo>/tmp-<id>/`). Never delete arbitrary external
    # parent dirs — that would wipe out callers' tmpdirs.
    local basename_parent
    basename_parent="$(basename "$parent_dir")"
    case "$basename_parent" in
        tmp-*) rm -rf "$parent_dir" 2>/dev/null || true ;;
        *)     ;;  # caller-owned parent — leave it alone
    esac
}

# Internal: bridge between the EXIT trap string and the named-args
# form above. The trap string already carries interpolated REPO + WT
# path (see the install line in with_scratch_worktree), so this is
# just a thin adapter. `set +u` keeps the trap robust against callers
# that run with `set -u` (a missing path would otherwise blow up
# here).
__scratch_worktree_trap() {
    set +u
    cleanup_scratch_worktree "$1" "$2"
}
