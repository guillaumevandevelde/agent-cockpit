#!/bin/bash
# Test harness for scripts/cockpit.sh
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Source the script without running main()
export COCKPIT_NO_MAIN=1
# shellcheck disable=SC1090
source "$SCRIPT_DIR/cockpit.sh"

echo "Task 1: source-guard + paths"
check "PROJECT_ROOT set"        '[ -n "$PROJECT_ROOT" ]'
check "LOG_DIR under root"      '[ "$LOG_DIR" = "$PROJECT_ROOT/logs" ]'
check "cmd_status is a function" 'declare -F cmd_status >/dev/null'

echo ""
echo "Task 2: prune_logs"
TMP_LOGS="$(mktemp -d)"
mkdir -p "$TMP_LOGS/backend"
touch "$TMP_LOGS/backend/run-old.log"  && touch -d "8 days ago" "$TMP_LOGS/backend/run-old.log"
touch "$TMP_LOGS/backend/run-new.log"
LOG_DIR="$TMP_LOGS" prune_logs
check "old run-log pruned"   '[ ! -f "$TMP_LOGS/backend/run-old.log" ]'
check "fresh run-log kept"   '[ -f "$TMP_LOGS/backend/run-new.log" ]'
rm -rf "$TMP_LOGS"

echo ""
echo "Task 3: pid helpers"
TMP_RUN="$(mktemp -d)"
PIDF="$TMP_RUN/x.pid"
check "is_running false when no pidfile" '! is_running "$PIDF"'
sleep 30 & SLEEP_PID=$!
echo "$SLEEP_PID" > "$PIDF"
check "is_running true for live pid"     'is_running "$PIDF"'
kill_tree "$SLEEP_PID"
check "is_running false after kill_tree"  '! kill -0 "$SLEEP_PID" 2>/dev/null'
rm -rf "$TMP_RUN"

echo ""
echo "Task 4: watch_service crash-loop guard"
TMP_W="$(mktemp -d)"
mkdir -p "$TMP_W"
# A command that always fails instantly -> must trip the crash-loop guard fast.
COCKPIT_BACKOFF_BASE=0 LOG_DIR="$TMP_W" \
    watch_service flap "exit 1" >/dev/null 2>&1
GUARD_HITS="$(grep -c 'crash-loop' "$TMP_W/supervisor.log" 2>/dev/null || echo 0)"
EXIT_LINES="$(grep -c 'flap exited' "$TMP_W/supervisor.log" 2>/dev/null || echo 0)"
check "crash-loop guard tripped"          '[ "$GUARD_HITS" -ge 1 ]'
check "logged ~5 exits before giving up"  '[ "$EXIT_LINES" -ge 5 ] && [ "$EXIT_LINES" -le 7 ]'
check "a run-log was written"             'ls "$TMP_W"/flap/run-*.log >/dev/null 2>&1'
rm -rf "$TMP_W"

echo ""
echo "Task 4b: crash-loop guard with slow-ish crashes (regression)"
TMP_W2="$(mktemp -d)"
# Each run takes ~0.4s then fails. With window=1, none count as healthy,
# so 5 consecutive crashes must trip the guard. The OLD code looped forever
# here; timeout guarantees a hang shows up as a failure instead of blocking.
timeout 15 bash -c '
    export COCKPIT_NO_MAIN=1
    source "'"$SCRIPT_DIR"'/cockpit.sh"
    COCKPIT_BACKOFF_BASE=0 COCKPIT_WINDOW=1 LOG_DIR="'"$TMP_W2"'" \
        watch_service slowflap "sleep 0.4; exit 1"
' >/dev/null 2>&1
RC=$?
GUARD2="$(grep -c 'crash-loop' "$TMP_W2/supervisor.log" 2>/dev/null || echo 0)"
check "slow-crash guard terminated (no hang)" '[ "$RC" -ne 124 ]'
check "slow-crash guard tripped"              '[ "$GUARD2" -ge 1 ]'
rm -rf "$TMP_W2"

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
