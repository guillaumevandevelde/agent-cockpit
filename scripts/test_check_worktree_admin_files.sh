#!/usr/bin/env bash
#
# test_check_worktree_admin_files.sh — harness for
# scripts/check-worktree-admin-files.sh (kanban card 7dd8a3dd…).
#
# Each case builds a throwaway `git init` repo, stages a specific shape, and
# asserts the SUT's verdict. Real-state assertions are SPECIFIC, per the
# CLAUDE.md note: the clean-state case greps the exact `^OK: no git
# per-worktree admin files are tracked` line, never a permissive
# `^OK:|WARNING:` alternation that would pass in both the broken and the
# fixed state (self-improve card e5136a3f).
#
# Coverage:
#   1. Clean repo               -> OK line, exit 0
#   2. Tracked `HEAD`           -> flagged, exit 0 advisory
#   3. Tracked `HEAD` --strict  -> flagged, exit 1
#   4. All ten tracked          -> all ten named in output
#   5. UNTRACKED `HEAD`         -> NOT flagged (tracked-only predicate)
#   6. `frontend/src/index.css` -> NOT flagged (root-anchored predicate)
#   7. Live repo regression pin -> this repo is clean under --strict
#   8. Non-git dir              -> exit 2
#   9. Unknown argument         -> exit 2

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-worktree-admin-files.sh"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PASS=0; FAIL=0
ok()  { echo "  ok: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Build a throwaway repo at $1; remaining args are paths to create + commit.
mkrepo() {
  local repo="$1"; shift
  mkdir -p "$repo"
  git -C "$repo" init -q --initial-branch=main
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  echo seed > "$repo/README.md"
  git -C "$repo" add README.md
  local p
  for p in "$@"; do
    mkdir -p "$(dirname "$repo/$p")"
    echo x > "$repo/$p"
    # -f so the repo's own .gitignore (if any) can't mask the fixture.
    git -C "$repo" add -f "$p"
  done
  git -C "$repo" commit -qm seed
}

echo "== 1. clean repo =="
mkrepo "$TMP/clean"
OUT="$("$SUT" --repo="$TMP/clean" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
# SPECIFIC clean-state assertion — not `^OK:|WARNING:`.
if grep -qE "^OK: no git per-worktree admin files are tracked" <<<"$OUT"; then
  ok "emits the exact clean-state OK line"
else
  bad "clean-state OK line missing; got: $OUT"
fi

echo "== 2. tracked HEAD is flagged (advisory) =="
mkrepo "$TMP/head" HEAD
OUT="$("$SUT" --repo="$TMP/head" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "advisory mode exits 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^WARNING: 1 git per-worktree admin file" <<<"$OUT"; then
  ok "warns with a count"
else
  bad "no WARNING line; got: $OUT"
fi
if grep -qE "^  - HEAD$" <<<"$OUT"; then ok "names HEAD"; else bad "HEAD not named"; fi
# The ambiguity symptom must be explained, not just the filename listed.
if grep -q "ambiguous argument" <<<"$OUT"; then
  ok "explains the git diff --quiet HEAD ambiguity"
else
  bad "output does not explain the ambiguity symptom"
fi

echo "== 3. --strict exits 1 =="
"$SUT" --repo="$TMP/head" --strict >/dev/null 2>&1; RC=$?
if [ "$RC" -eq 1 ]; then ok "exit 1"; else bad "expected exit 1, got $RC"; fi

echo "== 4. all ten are detected =="
mkrepo "$TMP/all" AUTO_MERGE HEAD MERGE_HEAD MERGE_MODE MERGE_MSG \
                 ORIG_HEAD commondir gitdir index index.lock
OUT="$("$SUT" --repo="$TMP/all" 2>&1)"
if grep -qE "^WARNING: 10 git per-worktree admin file" <<<"$OUT"; then
  ok "counts all ten"
else
  bad "expected a count of 10; got: $(grep '^WARNING' <<<"$OUT")"
fi
MISSING=""
for f in AUTO_MERGE HEAD MERGE_HEAD MERGE_MODE MERGE_MSG ORIG_HEAD commondir gitdir index index.lock; do
  grep -qE "^  - ${f//./\\.}$" <<<"$OUT" || MISSING="$MISSING $f"
done
if [ -z "$MISSING" ]; then ok "names each of the ten"; else bad "not named:$MISSING"; fi

echo "== 5. UNTRACKED admin-named file is NOT flagged =="
mkrepo "$TMP/untracked"
echo junk > "$TMP/untracked/HEAD"
echo junk > "$TMP/untracked/index"
OUT="$("$SUT" --repo="$TMP/untracked" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ] && grep -qE "^OK: no git per-worktree admin files are tracked" <<<"$OUT"; then
  ok "untracked files are harmless and stay unflagged"
else
  bad "untracked file was flagged (tracked-only predicate broken); got: $OUT"
fi

echo "== 6. non-root index.css / index.md are NOT flagged =="
mkrepo "$TMP/nested" frontend/src/index.css docs/index.md backend/app/index.py
OUT="$("$SUT" --repo="$TMP/nested" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ] && grep -qE "^OK: no git per-worktree admin files are tracked" <<<"$OUT"; then
  ok "root-anchored predicate ignores nested paths"
else
  bad "nested path false-positived; got: $OUT"
fi

echo "== 7. live repo is clean (regression pin) =="
OUT="$("$SUT" --repo="$REPO_ROOT" --strict 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then
  ok "this repo has no tracked worktree admin files"
else
  bad "THIS REPO is dirty — every ship is broken: $OUT"
fi

echo "== 8. non-git directory =="
mkdir -p "$TMP/notgit"
"$SUT" --repo="$TMP/notgit" >/dev/null 2>&1; RC=$?
if [ "$RC" -eq 2 ]; then ok "exit 2"; else bad "expected exit 2, got $RC"; fi

echo "== 9. unknown argument =="
"$SUT" --nope >/dev/null 2>&1; RC=$?
if [ "$RC" -eq 2 ]; then ok "exit 2"; else bad "expected exit 2, got $RC"; fi

echo
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
