#!/usr/bin/env bash
# Test harness for scripts/list-orphan-bridge-sessions.sh.
#
# Verifies the four filters that decide "orphan": Cockpit-spawned (COCKPIT_RUNTIME
# tmux env var), not claimed by a live kanban card (kanban_active_worktrees.py),
# old enough (ORPHAN_GRACE_S), and that the script never kills anything it flags.
#
# Uses REAL tmux sessions (uniquely-prefixed names, killed by exact name in
# cleanup — never `pkill -f`, see CLAUDE.md gotcha on shared-box self-matching)
# so the test exercises the actual `tmux show-environment` / `session_created`
# codepath instead of mocking it away.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

command -v tmux >/dev/null 2>&1 || { echo "tmux not available — skipping"; exit 0; }

RUN_ID="orphtest$$"
SPAWNED=()

cleanup() {
  for s in "${SPAWNED[@]:-}"; do
    [ -n "$s" ] && tmux kill-session -t "$s" >/dev/null 2>&1
  done
}
trap cleanup EXIT

# Args: session_name  [cockpit_runtime_value or "" for none]
spawn() {
  local name="$1" runtime="$2"
  local envargs=()
  [ -n "$runtime" ] && envargs=(-e "COCKPIT_RUNTIME=$runtime")
  tmux new-session -d -s "$name" "${envargs[@]}" 'sleep 60'
  SPAWNED+=("$name")
}

seed_kanban_db() {
  local db_path="$1"
  rm -f "$db_path"
  python3 - "$db_path" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.execute("""
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        project_key TEXT,
        title TEXT,
        column TEXT,
        claimed_by TEXT,
        claimed_at TEXT
    )
""")
con.commit(); con.close()
PY
}

insert_card() {
  local db="$1" id="$2" col="$3" claim="$4"
  python3 - "$db" "$id" "$col" "$claim" <<'PY'
import sqlite3, sys
db, cid, col, claim = sys.argv[1:5]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, column, claimed_by, claimed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (cid, "git-example-test", cid, col, claim, "2026-07-10T00:00:00"),
)
con.commit(); con.close()
PY
}

run_check() {
  local db="$1" grace="$2"
  KANBAN_DB="$db" ORPHAN_GRACE_S="$grace" bash "$SCRIPT_DIR/list-orphan-bridge-sessions.sh" 2>&1
}

echo "Task 1: Cockpit-spawned + unclaimed + past grace -> WOULD-FLAG"
T="$(mktemp -d)"
seed_kanban_db "$T/kanban.db"
name="${RUN_ID}-orphan1"
spawn "$name" "worktree"
out="$(run_check "$T/kanban.db" 0)"
check "flags the orphan session" "echo \"\$out\" | grep -qE 'WOULD-FLAG[[:space:]]+$name'"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Task 2: Cockpit-spawned + claimed by a live card -> not flagged"
T="$(mktemp -d)"
seed_kanban_db "$T/kanban.db"
name="${RUN_ID}-claimed1"
spawn "$name" "worktree"
insert_card "$T/kanban.db" "card-1" "engineer" "agent:$name"
out="$(run_check "$T/kanban.db" 0)"
check "does not flag a claimed session" "! echo \"\$out\" | grep -qE 'WOULD-FLAG[[:space:]]+$name'"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Task 3: NOT Cockpit-spawned (no COCKPIT_RUNTIME) -> never flagged, out of scope"
T="$(mktemp -d)"
seed_kanban_db "$T/kanban.db"
name="${RUN_ID}-plain1"
spawn "$name" ""
out="$(run_check "$T/kanban.db" 0)"
check "ignores a non-Cockpit tmux session" "! echo \"\$out\" | grep -qE 'WOULD-FLAG[[:space:]]+$name'"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Task 4: Cockpit-spawned + unclaimed but within grace window -> not flagged yet"
T="$(mktemp -d)"
seed_kanban_db "$T/kanban.db"
name="${RUN_ID}-fresh1"
spawn "$name" "worktree"
out="$(run_check "$T/kanban.db" 3600)"
check "grace period suppresses a brand-new orphan" "! echo \"\$out\" | grep -qE 'WOULD-FLAG[[:space:]]+$name'"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Task 5: missing kanban DB does not crash; still flags the orphan"
T="$(mktemp -d)"
name="${RUN_ID}-nodb1"
spawn "$name" "worktree"
out="$(run_check "/nonexistent/path/kanban.db" 0)"
check "flags even without a kanban DB" "echo \"\$out\" | grep -qE 'WOULD-FLAG[[:space:]]+$name'"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Task 6: report-only -- the flagged session is never actually killed"
T="$(mktemp -d)"
seed_kanban_db "$T/kanban.db"
name="${RUN_ID}-survives1"
spawn "$name" "worktree"
run_check "$T/kanban.db" 0 >/dev/null
check "flagged session is still alive after the check ran" \
    "tmux has-session -t '$name' 2>/dev/null"
tmux kill-session -t "$name" >/dev/null 2>&1
rm -rf "$T"

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
