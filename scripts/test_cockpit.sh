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
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
