#!/usr/bin/env bash
# Test harness for the concurrency hardening in .githooks/pre-push.
#
# The hook is written so that sourcing it defines its functions WITHOUT running
# the gate (the real run is guarded by `[ "${BASH_SOURCE[0]}" = "$0" ]`). Each
# test sources the hook inside its own subshell so the hook's `set -uo pipefail`
# and any fd/state changes never leak between tests.
#
# Run:  bash scripts/test_prepush_gate.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/../.githooks/pre-push"

pass=0; fail=0
ok()   { printf '  \033[32mok\033[0m   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
head() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

[ -f "$HOOK" ] || { echo "hook not found at $HOOK"; exit 2; }

# --- 1. gate_timed: success / timeout classification / passthrough -----------
head "gate_timed exit-code classification"
(
  source "$HOOK"
  gate_timed 5 1 -- true;            [ $? -eq 0 ]   || exit 21
  gate_timed 1 1 -- sleep 5;         [ $? -eq 124 ] || exit 22
  gate_timed 5 1 -- bash -c 'exit 7';[ $? -eq 7 ]   || exit 23
) && ok "success=0, over-budget=124, passthrough preserved" || bad "gate_timed classification (rc $?)"

# --- 2. gate_report: message + return per rc ---------------------------------
head "gate_report messaging"
(
  source "$HOOK"
  out="$(gate_report 'X' 0 2>&1)";   gate_report 'X' 0   >/dev/null 2>&1 || exit 31
  out="$(gate_report 'X' 124 2>&1)"; echo "$out" | grep -qi 'timed out' || exit 32
  gate_report 'X' 124 >/dev/null 2>&1 && exit 33   # must return non-zero
  out="$(gate_report 'X' 137 2>&1)"; echo "$out" | grep -qi 'timed out' || exit 34
  out="$(gate_report 'X' 5 2>&1)";   echo "$out" | grep -qi 'timed out' && exit 35  # not a timeout
  echo "$out" | grep -qi 'FAILED' || exit 36
) && ok "rc0 passes, 124/137 -> 'timed out' + fail, other -> FAILED" || bad "gate_report messaging (rc $?)"

# --- 3. lock serialization: two holders do not overlap -----------------------
head "flock serialization (no overlap)"
(
  source "$HOOK"
  tmp="$(mktemp -d)"; lock="$tmp/lock"; counter="$tmp/inside"
  : > "$counter"
  worker() {
    gate_acquire_lock "$lock"
    n=$(( $(cat "$counter") + 1 )); echo "$n" > "$counter"
    [ "$n" -gt 1 ] && echo OVERLAP > "$tmp/overlap"
    sleep 0.4
    echo 0 > "$counter"
    gate_release_lock
  }
  worker & worker & worker &
  wait
  [ -f "$tmp/overlap" ] && { rm -rf "$tmp"; exit 41; }
  rm -rf "$tmp"
) && ok "concurrent gate_acquire_lock holders never overlap" || bad "serialization overlap detected (rc $?)"

# --- 4. lock-wait-then-proceed when held past the wait budget ----------------
head "lock wait timeout -> proceed unserialized (warns, still returns 0)"
(
  export COCKPIT_GATE_LOCK_WAIT=1   # must be set BEFORE source (captured at load time)
  source "$HOOK"
  tmp="$(mktemp -d)"; lock="$tmp/lock"
  # Hold the lock from a SEPARATE process (real-world contention). A same-shell fd
  # would be inherited by the forked acquirer and self-granted — not a real race.
  ( exec 8>"$lock"; flock 8; sleep 5 ) & holder=$!
  sleep 0.3
  out="$(gate_acquire_lock "$lock" 2>&1)"; rc=$?
  kill "$holder" 2>/dev/null; wait "$holder" 2>/dev/null
  gate_release_lock; rm -rf "$tmp"
  [ "$rc" -eq 0 ] || exit 51
  echo "$out" | grep -qi 'WITHOUT serialization' || exit 52
) && ok "held lock -> waits, warns, proceeds (rc 0)" || bad "lock-wait fallback (rc $?)"

# --- 5. fd non-inheritance: children in the critical section lack the lock fd -
head "lock fd 200 is NOT inherited by heavy child processes"
(
  source "$HOOK"
  tmp="$(mktemp -d)"; lock="$tmp/lock"
  gate_acquire_lock "$lock"
  # Mimic how gate_run_* launch the heavy child: subshell with 200>&-.
  leaked="$( ( bash -c '[ -e /proc/self/fd/200 ] && echo LEAK || echo clean' ) 200>&- )"
  # And prove the parent still actually holds the lock while that ran:
  held=no
  ( flock -n 9 || echo BLOCKED ) 9>"$lock" | grep -q BLOCKED && held=yes
  gate_release_lock; rm -rf "$tmp"
  [ "$leaked" = clean ] || exit 61
  [ "$held" = yes ]     || exit 62
) && ok "child has no fd 200 while parent still holds the lock" || bad "fd inheritance / lock hold (rc $?)"

# --- 6. gate_lock_path resolves to an absolute shared lock file ---------------
head "gate_lock_path shape"
(
  source "$HOOK"
  p="$(gate_lock_path)"
  case "$p" in
    /*/cockpit-prepush.lock) : ;;
    *) exit 71 ;;
  esac
) && ok "absolute path ending in cockpit-prepush.lock" || bad "gate_lock_path (rc $?)"

# --- 7. GATE_LOCK_WAIT default must exceed the worst-case single-holder hold --
# gate_acquire_lock is taken ONCE and held across BOTH gate_run_backend and
# gate_run_frontend, each independently capped at GATE_RUN_TIMEOUT+GATE_KILL_AFTER.
# If a queued waiter's GATE_LOCK_WAIT is shorter than that combined worst case, it
# gives up mid-hold and "proceeds unserialized" -- stacking a second full
# backend+frontend run on top of the first. That doubles contention, slows both
# runs further, and pushes the NEXT waiter past its own budget too: a thundering
# herd that made concurrent pushes hang indefinitely.
head "GATE_LOCK_WAIT default safely exceeds worst-case backend+frontend hold"
(
  unset COCKPIT_GATE_LOCK_WAIT COCKPIT_GATE_RUN_TIMEOUT
  source "$HOOK"
  worst_case=$(( (GATE_RUN_TIMEOUT + GATE_KILL_AFTER) * 2 ))
  [ "$GATE_LOCK_WAIT" -gt "$worst_case" ] || exit 81
) && ok "default lock wait > worst-case sequential backend+frontend hold" || bad "GATE_LOCK_WAIT vs GATE_RUN_TIMEOUT relationship (rc $?)"

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
