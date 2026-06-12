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
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
