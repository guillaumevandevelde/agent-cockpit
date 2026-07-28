#!/usr/bin/env bash
# Test harness for the direct-mode ship recipe's 0-byte-index guard.
#
# Background (kanban card 608e2a27…): the throwaway merge worktree lives in
# the shared `.git/worktrees/ship-merge-$$` slot. A session that aborts
# mid-ship can leave that slot's `index` truncated to 0 bytes. `git worktree
# add --detach` still reports success, so the corruption first surfaces on the
# *merge*, as `fatal: …/index: index file smaller than expected` — and
# `git worktree remove --force` then refuses with `is not a working tree`,
# orphaning the slot. The recipe now detects the 0-byte index right after
# `git worktree add` and rebuilds it with `git read-tree HEAD`.
#
# `backend/tests/test_ship_recipe_drift.py` pins that the guard *text* exists
# in both mirrors and sits in the executable path. This harness pins that the
# guard *works*: it extracts the real block from
# `.claude/skills/git-ship/SKILL.md` (never a copy — a copy would drift) and
# runs it against a scratch repo with a deliberately corrupted slot index.
#
# Tasks:
#   1. extraction sanity — the block really came out of SKILL.md and carries
#      both the detection and the `read-tree HEAD` recovery. Without this, a
#      failed extraction would make tasks 3 and 4 pass vacuously.
#   2. baseline — WITHOUT the guard, a 0-byte slot index makes the merge die
#      with exactly `index file smaller than expected`.
#   3. recovery — WITH the guard, the same corrupted slot merges successfully
#      and the merge commit lands.
#   4. no-op — on a healthy slot the guard is silent and does not disturb the
#      merge.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL="$REPO_ROOT/.claude/skills/git-ship/SKILL.md"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

GUARD="$TMP/guard.sh"

# Extract the guard block from SKILL.md §4a: everything from the slot-gitdir
# resolution up to (excluding) the merge handler that follows it.
awk '
  /^WT_GITDIR=\$\(git -C "\$WT" rev-parse --absolute-git-dir\)$/ { on=1 }
  on && /^if ! git -C "\$WT" merge --no-ff/ { on=0 }
  on { print }
' "$SKILL" > "$GUARD"

# ----------------------------------------------------------------------------
echo "Task 1: extraction sanity — guard block came out of SKILL.md"
check "extracted block is non-empty" '[ -s "$GUARD" ]'
check "carries the 0-byte detection" \
  'grep -qF "if [ ! -s \"\$WT_GITDIR/index\" ]; then" "$GUARD"'
check "carries the read-tree HEAD recovery" \
  'grep -qF "git -C \"\$WT\" read-tree HEAD" "$GUARD"'

# ----------------------------------------------------------------------------
# Build a scratch repo shaped like the ship scenario: a base branch to merge
# into and a feature branch to merge. Returns the repo path on stdout.
make_repo() {
  local repo="$1"
  git init -q "$repo"
  git -C "$repo" config user.email harness@example.invalid
  git -C "$repo" config user.name  harness
  echo base > "$repo/a.txt"
  git -C "$repo" add -A
  git -C "$repo" commit -qm init
  BASE_BRANCH="$(git -C "$repo" rev-parse --abbrev-ref HEAD)"
  git -C "$repo" checkout -q -b feature
  echo feature >> "$repo/a.txt"
  git -C "$repo" commit -qam feat
  git -C "$repo" checkout -q "$BASE_BRANCH"
}

# Create the ship-merge slot exactly as the recipe does, and echo its path.
make_slot() {
  local repo="$1" tag="$2" wt
  wt="$(git -C "$repo" rev-parse --git-common-dir)/worktrees/ship-merge-$tag"
  case "$wt" in /*) ;; *) wt="$repo/$wt" ;; esac
  git -C "$repo" worktree add --detach "$wt" "$BASE_BRANCH" >/dev/null 2>&1
  printf '%s\n' "$wt"
}

corrupt_index() {
  local wt="$1" gitdir
  gitdir="$(git -C "$wt" rev-parse --absolute-git-dir)"
  : > "$gitdir/index"
}

# ----------------------------------------------------------------------------
echo "Task 2: baseline — a 0-byte slot index breaks the unguarded merge"
R2="$TMP/baseline"; make_repo "$R2"
WT2="$(make_slot "$R2" 2)"
corrupt_index "$WT2"
out2=$(git -C "$WT2" merge --no-ff feature -m "Merge feature" 2>&1)
rc2=$?
check "unguarded merge fails" '[ "$rc2" -ne 0 ]'
check "fails with 'index file smaller than expected'" \
  'printf "%s" "$out2" | grep -qF "index file smaller than expected"'

# ----------------------------------------------------------------------------
echo "Task 3: recovery — the guard rebuilds the index and the merge lands"
R3="$TMP/recovery"; make_repo "$R3"
WT3="$(make_slot "$R3" 3)"
corrupt_index "$WT3"
guard_out3=$( WT="$WT3" bash "$GUARD" 2>&1 )
guard_rc3=$?
check "guard exits 0" '[ "$guard_rc3" -eq 0 ]'
check "guard warns about the 0-byte index" \
  'printf "%s" "$guard_out3" | grep -qF "0-byte index"'
check "index is rebuilt (non-empty)" \
  '[ -s "$(git -C "$WT3" rev-parse --absolute-git-dir)/index" ]'
out3=$(git -C "$WT3" merge --no-ff feature -m "Merge feature" 2>&1)
rc3=$?
check "merge now succeeds" '[ "$rc3" -eq 0 ]'
check "merge commit landed" \
  'git -C "$WT3" log -1 --pretty=%s | grep -qx "Merge feature"'

# ----------------------------------------------------------------------------
echo "Task 4: no-op — a healthy slot is left untouched"
R4="$TMP/healthy"; make_repo "$R4"
WT4="$(make_slot "$R4" 4)"
before4="$(git -C "$WT4" rev-parse --absolute-git-dir)/index"
sum_before=$(cksum < "$before4")
guard_out4=$( WT="$WT4" bash "$GUARD" 2>&1 )
guard_rc4=$?
check "guard exits 0 on a healthy slot" '[ "$guard_rc4" -eq 0 ]'
check "guard is silent on a healthy slot" '[ -z "$guard_out4" ]'
check "index left byte-identical" '[ "$sum_before" = "$(cksum < "$before4")" ]'
out4=$(git -C "$WT4" merge --no-ff feature -m "Merge feature" 2>&1)
rc4=$?
check "merge succeeds on a healthy slot" '[ "$rc4" -eq 0 ]'

# ----------------------------------------------------------------------------
echo
echo "PASS: $PASS  FAIL: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
