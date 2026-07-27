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

# A non-zero shell exit still runs EXIT cleanup.
(
  source "$LIB"
  with_scratch_worktree "$REPO" WT >/dev/null
  exit 17
) || true
check "non-zero exit leaves no tmp-* parent" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'

# A dispatched harness may be terminated by the supervisor. Verify that a TERM
# signal reaches the EXIT cleanup instead of leaving the helper-owned parent.
OUT_SIGNAL="$(mktemp)"
bash -c '
  source "$1"
  with_scratch_worktree "$2" WT >/dev/null
  printf "%s\n" "$WT" > "$3"
  while :; do sleep 1; done
' _ "$LIB" "$REPO" "$OUT_SIGNAL" &
SIGNAL_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -s "$OUT_SIGNAL" ] && break
    sleep 0.05
done
kill -TERM "$SIGNAL_PID"
wait "$SIGNAL_PID" 2>/dev/null || true
check "TERM signal leaves no tmp-* parent" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
rm -f "$OUT_SIGNAL"

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

# -----------------------------------------------------------------------------
echo "Task 5: caller can choose the scratch worktree source ref"

# The generic helper defaults to HEAD, but callers such as the token-saver
# measurement need a stable master baseline even when invoked from a feature
# branch. The optional third argument must be honored.
git -C "$REPO" checkout -qb feature
echo feature > "$REPO/seed.txt"
git -C "$REPO" add seed.txt
git -C "$REPO" commit -qm feature

OUT5="$(mktemp)"
(
  source "$LIB"
  with_scratch_worktree "$REPO" WT master
  cat "$WT/seed.txt" > "$OUT5"
)
check "explicit master ref wins over feature-branch HEAD" \
    '[ "$(cat "$OUT5")" = "seed" ]'
check "explicit-ref scratch still leaves no tmp-* parent" \
    '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
rm -f "$OUT5"

# -----------------------------------------------------------------------------
echo "Task 6: helper survives being sourced from zsh (dispatch-shell parity)"

# Regression for card 95f5199c…: the dispatch shell is zsh, where `path`
# is a SPECIAL array parameter bound to $PATH. A `local path="$2"` inside
# cleanup_scratch_worktree therefore overwrote PATH with the worktree
# path for the rest of that function, so every external binary in the
# cleanup body (`dirname`, `basename`, `git`, `rm`) vanished:
#
#     cleanup_scratch_worktree:12: command not found: dirname
#
# Net effect was worse than the naive `mktemp -d` pattern this helper
# replaced: the worktree stayed registered in `git worktree list` AND the
# `tmp-<id>` parent stayed in the working tree, while callers trusted the
# trap and did not clean up by hand.
#
# These assertions must run under zsh — the bug is invisible in bash,
# where `path` is an ordinary scalar. That shell asymmetry is exactly why
# the bash-only harness above stayed green through the whole regression.
if command -v zsh >/dev/null 2>&1; then
    OUT6="$(mktemp)"
    ERR6="$(mktemp)"
    zsh -c '
      source "$1"
      with_scratch_worktree "$2" WT >/dev/null
      printf "%s\n" "$WT" > "$3"
      echo "payload" > "$WT/payload.txt"
    ' _ "$LIB" "$REPO" "$OUT6" 2>"$ERR6"

    check "zsh: cleanup emits no 'command not found' on the trap path" \
        '! grep -q "command not found" "$ERR6"'
    check "zsh: EXIT trap removed the tmp-<id> parent" \
        '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
    check "zsh: EXIT trap removed the worktree subdir" \
        '[ ! -e "$(cat "$OUT6" 2>/dev/null)" ]'
    check "zsh: worktree is deregistered from git worktree list" \
        '! git -C "$REPO" worktree list --porcelain | grep -qF "$(cat "$OUT6" 2>/dev/null)"'

    # Second dynamic path: an EXPLICIT cleanup call (not via the trap).
    # Asserting on $PATH after the call returns would be tautological —
    # `local path=` is function-scoped, so PATH is restored on return and
    # the assertion passes in both the broken and fixed state. Assert on
    # the call's own stderr instead, which is where the clobber surfaces.
    ERR6B="$(mktemp)"
    zsh -c '
      source "$1"
      with_scratch_worktree "$2" WT >/dev/null
      cleanup_scratch_worktree "$2" "$WT"
    ' _ "$LIB" "$REPO" 2>"$ERR6B"
    check "zsh: explicit cleanup call emits no 'command not found'" \
        '! grep -q "command not found" "$ERR6B"'

    # Static guard: no `local` declaration in the lib may shadow a zsh
    # special parameter. This is the assertion that would have caught the
    # original bug at review time, in any shell. Two pattern requirements:
    #   - it must cross earlier declarators on the same line, because the
    #     original bug lived in the SECOND slot (`local repo="$1" path="$2"`);
    #   - it must skip comment lines, or the in-lib note documenting this very
    #     bug matches itself and the guard can never go green.
    check "lib declares no zsh-special parameter as a local" \
        '! grep -nE "^[[:space:]]*local\b[^#]*(^|[[:space:]])(path|cdpath|fpath|manpath|mailpath|module_path|argv|status)=" "$LIB"'

    # ---- additional regressions for the second/third bugs the card hid
    #
    # While the `dirname` symptom was the visible failure, the
    # investigation turned up two deeper problems that the symptom was
    # actually MASKING in zsh:
    #
    #   (A) Under zsh, `trap ... EXIT` set inside a function is
    #       function-scoped and fires the moment the function returns.
    #       That destroys the just-created worktree before the caller
    #       can use it. Pre-fix this was invisible because the cleanup
    #       body had already aborted on `dirname`, so the worktree
    #       stayed alive by accident.
    #
    #   (B) In both bash and zsh, every call to with_scratch_worktree
    #       installed a NEW trap string. A second call overwrote the
    #       first call's trap, so the first worktree was never
    #       cleaned — exactly the accumulation the helper exists to
    #       prevent. The reviewer's reproduction (two calls in one
    #       session) hit this on the bash path too.

    # (A) zsh: worktree must be USABLE for the entire body of the
    # caller, not just for the return of with_scratch_worktree. We
    # test this by doing real work in the worktree (read a file,
    # which the previous Task 1-5 never did) and asserting it
    # succeeds. The pre-fix behavior (with POSIX_TRAPS applied
    # post-fix) was: worktree created, function returns, trap
    # immediately fires, worktree gone, `cat` fails. So this would
    # fail under any fix that DIDN'T also set POSIX_TRAPS.
    # Use `seed.txt` (committed in the test repo setup above) so the
    # assertion is self-contained and doesn't rely on a file that's
    # only present in the project's own working tree.
    OUT6C="$(mktemp)"
    zsh -c '
      source "$1"
      with_scratch_worktree "$2" WT >/dev/null
      if head -1 "$WT/seed.txt" > "$3" 2>/dev/null; then
        echo READABLE > "$3.ok"
      else
        echo UNREADABLE > "$3.ok"
      fi
    ' _ "$LIB" "$REPO" "$OUT6C" 2>/dev/null
    check "zsh: worktree is usable AFTER with_scratch_worktree returns" \
        '[ "$(cat "$OUT6C.ok" 2>/dev/null)" = "READABLE" ]'
    [ -s "$OUT6C" ] && head -1 "$OUT6C" >/dev/null  # silent the read

    # (B) Two sequential calls in the same shell, then shell exit:
    # neither scratch may leak. Runs in BOTH shells because the
    # trap-overwrite bug is shell-agnostic. The trap-overwrite leak
    # only surfaces if the second call's `trap ... EXIT` overwrites the
    # first call's; the registry fix below keeps both clean.
    #
    # Note: we assert on the parent-dir count (the actual leak
    # symptom) rather than the registered-worktree count, because
    # under the pre-fix PATH-clobber `git worktree add` itself dies
    # before any worktree is registered — so a "registered = 0"
    # assertion would be green even with leaked parent dirs. The
    # dir count is the load-bearing check.
    for shell in bash zsh; do
        if ! command -v "$shell" >/dev/null 2>&1; then continue; fi
        "$shell" -c '
          source "$1"
          with_scratch_worktree "$2" WT1 >/dev/null
          with_scratch_worktree "$2" WT2 >/dev/null
        ' _ "$LIB" "$REPO" >/dev/null 2>&1
        check "$shell: two sequential calls leave no tmp-* parent" \
            '[ -z "$(cd "$REPO" && ls -d tmp-* 2>/dev/null || true)" ]'
    done

    # Park the test scratch outside the repo to keep `tmp-*` from
    # accumulating in $PWD across re-runs. `rm` is deny-listed in this
    # project, so the test runner never deletes files — it just moves
    # them aside. The mktemp names are unique per run.
    [ -n "$OUT6"   ] && mv "$OUT6"   "$OUT6.parked"   2>/dev/null
    [ -n "$ERR6"   ] && mv "$ERR6"   "$ERR6.parked"   2>/dev/null
    [ -n "$ERR6B"  ] && mv "$ERR6B"  "$ERR6B.parked"  2>/dev/null
    [ -n "$OUT6C"  ] && mv "$OUT6C"  "$OUT6C.parked"  2>/dev/null
    [ -n "$OUT6C.ok" ] && mv "$OUT6C.ok" "$OUT6C.ok.parked" 2>/dev/null
else
    echo "  skip: zsh not installed — dispatch-shell parity unverified"
fi

# ----------------------------------------------------------------------------
echo ""
echo "Summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
