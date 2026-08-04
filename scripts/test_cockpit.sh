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
echo "Task 3b: is_running identity check (PID-reuse safety)"
TMP_ID="$(mktemp -d)"
PIDF2="$TMP_ID/sup.pid"
sleep 60 & FOREIGN=$!
echo "$FOREIGN" > "$PIDF2"
check "is_running true without pattern"          'is_running "$PIDF2"'
check "is_running false for foreign cmdline"     '! is_running "$PIDF2" "cockpit.sh __supervisor"'
check "is_running true for matching cmdline"     'is_running "$PIDF2" "sleep 60"'
kill "$FOREIGN" 2>/dev/null
rm -rf "$TMP_ID"

echo ""
echo "Task 3c: ensure_fresh_boot wipes pid files from a previous boot"
TMP_B="$(mktemp -d)"
mkdir -p "$TMP_B/.run"
echo 99999 > "$TMP_B/.run/supervisor.pid"
echo "old-boot-id-from-before" > "$TMP_B/.run/boot_id"
( RUN_DIR="$TMP_B/.run" LOG_DIR="$TMP_B"; ensure_fresh_boot )
check "stale pid file removed on boot change"  '[ ! -f "$TMP_B/.run/supervisor.pid" ]'
check "boot_id refreshed"                      '[ "$(cat "$TMP_B/.run/boot_id")" != "old-boot-id-from-before" ]'
# Same boot id => pid files are left untouched.
echo 12345 > "$TMP_B/.run/frontend.pid"
( RUN_DIR="$TMP_B/.run" LOG_DIR="$TMP_B"; ensure_fresh_boot )
check "pid file kept when boot id unchanged"   '[ -f "$TMP_B/.run/frontend.pid" ]'
rm -rf "$TMP_B"

echo ""
echo "Task 3d: kill_tree reaps deep descendants (multi-child pgrep fix)"
TMP_K="$(mktemp -d)"
# Tree: root bash -> (sleep, inner bash) ; inner bash -> grandchild sleep.
# The old space-separated pgrep -P missed the grandchild.
setsid bash -c '
  sleep 300 &
  bash -c "sleep 300 & echo \$! > '"$TMP_K"'/gc.pid; wait" &
  wait
' >/dev/null 2>&1 &
ROOT=$!
sleep 1
GC="$(cat "$TMP_K/gc.pid" 2>/dev/null)"
check "grandchild was spawned"          '[ -n "$GC" ] && kill -0 "$GC" 2>/dev/null'
kill_tree "$ROOT"
sleep 1
check "kill_tree reaped grandchild"     '[ -n "$GC" ] && ! kill -0 "$GC" 2>/dev/null'
check "kill_tree reaped root"           '! kill -0 "$ROOT" 2>/dev/null'
pkill -f "sleep 300" 2>/dev/null || true
rm -rf "$TMP_K"

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
echo "Task 4c: watch_service stops cleanly on TERM (no orphan/respawn)"
TMP_W3="$(mktemp -d)"
( export COCKPIT_NO_MAIN=1
  source "$SCRIPT_DIR/cockpit.sh"
  LOG_DIR="$TMP_W3" RUN_DIR="$TMP_W3/.run" watch_service longrun "sleep 60" ) &
WATCHER=$!
sleep 2
CHILD_BEFORE="$(cat "$TMP_W3/.run/longrun.pid" 2>/dev/null)"
kill -TERM "$WATCHER" 2>/dev/null
sleep 3
check "watcher exited on TERM"        '! kill -0 "$WATCHER" 2>/dev/null'
check "child reaped (not running)"    '[ -n "$CHILD_BEFORE" ] && ! kill -0 "$CHILD_BEFORE" 2>/dev/null'
check "pidfile removed on shutdown"   '[ ! -f "$TMP_W3/.run/longrun.pid" ]'
# Make sure no stray sleep 60 from this test lingers
pkill -f "sleep 60" 2>/dev/null || true
rm -rf "$TMP_W3"

echo ""
echo "Task 5: start/status/stop lifecycle (injected commands)"
TMP_L="$(mktemp -d)"
export COCKPIT_BACKEND_CMD="sleep 1000"
export COCKPIT_FRONTEND_CMD="sleep 1000"
# Run the real CLI in a subshell with isolated dirs.
run_cli() { LOG_DIR="$TMP_L" RUN_DIR="$TMP_L/.run" bash "$SCRIPT_DIR/cockpit.sh" "$@"; }

run_cli start >/dev/null 2>&1
sleep 2
SUP_PID="$(cat "$TMP_L/.run/supervisor.pid" 2>/dev/null)"
check "supervisor pid recorded"   '[ -n "$SUP_PID" ] && kill -0 "$SUP_PID" 2>/dev/null'
check "status reports running"    'run_cli status 2>&1 | grep -qi "running"'
# Kill backend child -> supervisor must respawn it.
BK_PID="$(cat "$TMP_L/.run/backend.pid" 2>/dev/null)"
kill "$BK_PID" 2>/dev/null
sleep 3
BK_PID2="$(cat "$TMP_L/.run/backend.pid" 2>/dev/null)"
check "backend respawned (new pid)" '[ -n "$BK_PID2" ] && [ "$BK_PID2" != "$BK_PID" ] && kill -0 "$BK_PID2" 2>/dev/null'
run_cli stop >/dev/null 2>&1
sleep 1
check "supervisor gone after stop" '! kill -0 "$SUP_PID" 2>/dev/null'
check "stop is idempotent"          'run_cli stop >/dev/null 2>&1'
unset COCKPIT_BACKEND_CMD COCKPIT_FRONTEND_CMD
rm -rf "$TMP_L"

echo ""
echo "Task 5b: litellm sidecar opt-in (no service when no config present)"
# Status reports the litellm column either way (so operators see why it's not
# running), but no service is started and no extra pid is created unless the
# operator explicitly dropped a config on the expected path.
TMP_O="$(mktemp -d)"
unset LITELLM_CONFIG_PATH
(
    unset COCKPIT_BACKEND_CMD COCKPIT_FRONTEND_CMD LITELLM_CONFIG_PATH
    export LITELLM_CONFIG_PATH="$TMP_O/__nonexistent__.yaml"
    LOG_DIR="$TMP_O" RUN_DIR="$TMP_O/.run" bash "$SCRIPT_DIR/cockpit.sh" status > "$TMP_O/out" 2>&1
)
check "status prints litellm column"              'grep -q "^litellm:" "$TMP_O/out"'
check "status says 'not configured' without cfg"  'grep -q "not configured" "$TMP_O/out"'
check "no litellm pid written without config"     '[ ! -f "$TMP_O/.run/litellm.pid" ]'
# Full lifecycle with injected cmds and no config: still no service spawned,
# still clean stop, status unchanged.
(
    export COCKPIT_BACKEND_CMD="sleep 1000"
    export COCKPIT_FRONTEND_CMD="sleep 1000"
    unset LITELLM_CONFIG_PATH
    export LITELLM_CONFIG_PATH="$TMP_O/__nonexistent__.yaml"
    LOG_DIR="$TMP_O" RUN_DIR="$TMP_O/.run" bash "$SCRIPT_DIR/cockpit.sh" start >/dev/null 2>&1
    sleep 1
    LOG_DIR="$TMP_O" RUN_DIR="$TMP_O/.run" bash "$SCRIPT_DIR/cockpit.sh" status > "$TMP_O/out2" 2>&1
    LOG_DIR="$TMP_O" RUN_DIR="$TMP_O/.run" bash "$SCRIPT_DIR/cockpit.sh" stop  >/dev/null 2>&1
)
check "no litellm pid after start (no config)"    '[ ! -f "$TMP_O/.run/litellm.pid" ]'
check "status unchanged under full lifecycle"     'grep -q "not configured" "$TMP_O/out2"'
rm -rf "$TMP_O"
unset COCKPIT_BACKEND_CMD COCKPIT_FRONTEND_CMD LITELLM_CONFIG_PATH

echo ""
echo "Task 5c: litellm sidecar opt-in (config present => status flips to 'stopped')"
# Drop a fixture config, run status — supervisor reads LITELLM_CONFIG_PATH
# and prints 'config present but service not running' because no supervisor is
# actually started (status doesn't spawn). The flip is the proof: the column
# reads from the config existence, not from a watched pid file.
TMP_O2="$(mktemp -d)"
CFG="$TMP_O2/config.yaml"
touch "$CFG"
(
    unset COCKPIT_BACKEND_CMD COCKPIT_FRONTEND_CMD
    export LITELLM_CONFIG_PATH="$CFG"
    LOG_DIR="$TMP_O2" RUN_DIR="$TMP_O2/.run" bash "$SCRIPT_DIR/cockpit.sh" status > "$TMP_O2/out" 2>&1
)
check "status says 'config present' with cfg"  'grep -q "config present" "$TMP_O2/out"'
rm -rf "$TMP_O2"
unset LITELLM_CONFIG_PATH

echo ""
echo "Task 5d: should_start_litellm opt-in helper (source-mode unit tests)"
# Three laws, in order: (a) injected backend cmd => always false; (b) no
# config file => false; (c) real config file + no injected cmds => true.
TMP_O3="$(mktemp -d)"
CFG3="$TMP_O3/config.yaml"
touch "$CFG3"
(
    export COCKPIT_NO_MAIN=1
    source "$SCRIPT_DIR/cockpit.sh"
    export COCKPIT_BACKEND_CMD="sleep 1000"
    export LITELLM_CONFIG_PATH="$CFG3"
    check "should_start_litellm false under injected backend cmd" '! should_start_litellm'
    unset COCKPIT_BACKEND_CMD
    export LITELLM_CONFIG_PATH="$TMP_O3/__missing__.yaml"
    check "should_start_litellm false when config missing"        '! should_start_litellm'
    export LITELLM_CONFIG_PATH="$CFG3"
    check "should_start_litellm true with config + default cmds"  'should_start_litellm'
)
# Smoke-test the cmd/url builders — they're shell-quoted so a path mismatch
# is loud in the failure log.
(
    export COCKPIT_NO_MAIN=1
    unset LITELLM_CONFIG_PATH LITELLM_VENV LITELLM_PORT LITELLM_REQUIREMENTS
    source "$SCRIPT_DIR/cockpit.sh"
    CMD=$(default_litellm_cmd)
    URL=$(default_litellm_health_url)
    check "default_litellm_cmd activates venv"     '[ -n "$CMD" ] && [[ "$CMD" == *"$LITELLM_VENV/bin/activate"* ]]'
    check "default_litellm_health_url is loopback" '[[ "$URL" == http://127.0.0.1:* ]]'
)
rm -rf "$TMP_O3"
unset LITELLM_CONFIG_PATH

echo ""
echo "Task 6: run_merged_branches_sweeper actually runs the sweeper"
# Regression: the nudge referenced an undefined $REPO_ROOT (cockpit.sh defines
# PROJECT_ROOT), so under `set -u` the command substitution died in its
# subshell — every `cockpit.sh start` printed "REPO_ROOT: unbound variable" and
# silently took the "sweeper niet kunnen draaien" path. Stub the sweeper so the
# assertions are hermetic (no git, no network).
TMP_SW="$(mktemp -d)"
mkdir -p "$TMP_SW/bin" "$TMP_SW/logs"
cat > "$TMP_SW/bin/sweep_merged_remote_branches.py" <<'STUB'
import sys, json
# Echo the --repo we were handed so the test can assert it is a real path.
repo = sys.argv[sys.argv.index("--repo") + 1] if "--repo" in sys.argv else ""
print(json.dumps({"repo_path": repo, "totals": {"fully_merged": 3}}))
STUB
chmod +x "$TMP_SW/bin/sweep_merged_remote_branches.py"
SW_OUT="$TMP_SW/out.txt"
(
    export COCKPIT_NO_MAIN=1
    source "$SCRIPT_DIR/cockpit.sh"
    SCRIPT_DIR="$TMP_SW/bin"
    LOG_DIR="$TMP_SW/logs"
    unset COCKPIT_SKIP_REMOTE_SWEEP
    run_merged_branches_sweeper
) > "$SW_OUT" 2>&1
check "no unbound-variable error"        '! grep -q "unbound variable" "$SW_OUT"'
check "sweeper hit-count is reported"    'grep -q "3 volledig gemergede branch(es)" "$SW_OUT"'
check "sweeper did not take skip path"   '! grep -q "niet kunnen draaien" "$TMP_SW/logs/supervisor.log" 2>/dev/null'
check "sweeper got the repo root"        'grep -q -- "--repo \"$PROJECT_ROOT\"" "$SW_OUT"'
# Opt-out still wins.
(
    export COCKPIT_NO_MAIN=1
    source "$SCRIPT_DIR/cockpit.sh"
    SCRIPT_DIR="$TMP_SW/bin"
    LOG_DIR="$TMP_SW/logs"
    export COCKPIT_SKIP_REMOTE_SWEEP=1
    run_merged_branches_sweeper
) > "$TMP_SW/out2.txt" 2>&1
check "COCKPIT_SKIP_REMOTE_SWEEP silences the nudge" '[ ! -s "$TMP_SW/out2.txt" ]'
rm -rf "$TMP_SW"

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
