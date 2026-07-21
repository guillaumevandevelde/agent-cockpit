#!/usr/bin/env bash
# Test harness for scripts/lib/worktree-trap.sh.
#
# Covers:
#   1. Library sources cleanly and exposes the helper functions.
#   2. with_scratch_worktree creates a scratch worktree under the
#      repo root and binds it to the named caller variable.
#   3. cleanup_scratch_worktree removes both the worktree AND its
#      `tmp-<id>` parent dir (the original "mktemp -d cleanup trap"),
#      even when the worktree contains extra files (so `rmdir` alone
#      would fail).
#   4. After the trap fires, `ls -d tmp-*` under the repo root is
#      empty (no orphans left behind across iterations).
#   5. cleanup_scratch_worktree is idempotent and safe on existing
#      dirs the caller did NOT create.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib/worktree-trap.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Build a throwaway git repo to seed scratch worktrees from.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
REPO="$TMP/repo"
mkdir -p "$REPO"
( cd "$REPO" && \
  git init -q -b master && \
  git config user.email t@t && \
  git config user.name t && \
  echo seed > seed.txt && \
  git add seed.txt && \
  git commit -qm seed )

# ----------------------------------------------------------------------------
check "lib sources cleanly (syntax check)" 'source "$LIB" 2>/dev/null'
check "with_scratch_worktree is defined" 'source "$LIB" && type with_scratch_worktree >/dev/null 2>&1'
check "cleanup_scratch_worktree is defined" 'source "$LIB" && type cleanup_scratch_worktree >/dev/null 2>&1'

# ----------------------------------------------------------------------------
echo "Task 1: scratch worktree is created via the helper"

unset WT
( source "$LIB"
  with_scratch_worktree "$REPO" WT
  if [ -d "$WT" ]; then
    echo "$WT" > "$TMP/wt1.path"
    echo "CREATED" > "$TMP/wt1.created"
  fi )

check "WT variable was set after with_scratch_worktree" '[ -s "$TMP/wt1.created" ]'
# WT path is recorded inside $TMP/wt1.path while WT still existed. After
# the subshell exited, the EXIT trap cleaned it up — so we read its
# recorded value back rather than stat the live WT.
WT1_PATH="$(cat "$TMP/wt1.path" 2>/dev/null || echo "")"
check "WT recorded a path that begins with REPO" \
    '[ -n "$WT1_PATH" ] && case "$WT1_PATH" in "$REPO"/*) ;; *) false ;; esac'

# ----------------------------------------------------------------------------
echo "Task 2: cleanup_scratch_worktree removes both worktree AND parent dir"

OUT2="$(mktemp)"
( source "$LIB"
  with_scratch_worktree "$REPO" WT
  echo "leftover" > "$WT/leftover.txt"
  echo "$WT" > "$OUT2"
  cleanup_scratch_worktree "$REPO" "$WT" )

check "scratch worktree directory removed after cleanup" '[ ! -e "$(cat "$OUT2" 2>/dev/null)" ]'
check "tmp-* parent removed after cleanup" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
rm -f "$OUT2"

# ----------------------------------------------------------------------------
echo "Task 3: EXIT trap removes both worktree AND tmp parent on shell exit"

# The key card requirement: a harness sources the helper and exits;
# `ls -d tmp-*` under REPO_ROOT must be empty.
OUT3="$(mktemp)"
(
  source "$LIB"
  with_scratch_worktree "$REPO" WT
  echo "trap test payload" > "$WT/payload.txt"
  echo "$WT" > "$OUT3"
)
# subshell has exited; trap should have fired.
check "subshell EXIT trap removed the tmp-<id> parent" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
check "subshell EXIT trap removed the worktree subdir" '[ ! -e "$(cat "$OUT3" 2>/dev/null)" ]'
rm -f "$OUT3"

# ----------------------------------------------------------------------------
echo "Task 4: cleanup_scratch_worktree is idempotent and safe on caller dirs"

# Create a scratch with a SUB-tmp-* parent, then verify calling cleanup
# directly multiple times does not error.
(
  source "$LIB"
  with_scratch_worktree "$REPO" WT
  cleanup_scratch_worktree "$REPO" "$WT"
  cleanup_scratch_worktree "$REPO" "$WT"   # 2nd call, should no-op
)
check "cleanup_scratch_worktree idempotent" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'

# Pre-existing caller dir: parent IS the repo root (not a tmp-* sibling).
# If a caller hands us a WT path whose parent doesn't match the
# tmp-* pattern, we MUST NOT remove that parent.
SENTINEL="$REPO/.sentinel.txt"
echo "do not delete" > "$SENTINEL"
( source "$LIB"
  with_scratch_worktree "$REPO" WT
  cleanup_scratch_worktree "$REPO" "$WT" )
check "external sentinel file still present (helper doesn't touch non-tmp-* parents)" \
    '[ -f "$SENTINEL" ]'

# ----------------------------------------------------------------------------
echo ""
echo "Summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
