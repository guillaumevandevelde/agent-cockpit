#!/usr/bin/env bash
# Test harness for scripts/sweep_orphaned_parents_awaiting_subtasks.py.
#
# Exercises the orphaned-parent sweeper against synthetic SQLite fixtures
# in a tempdir, so the tests stay green regardless of the board's real
# state. The real-board check is a final optional task — the production
# kanban.db may legitimately carry historic orphans from before the fix
# (kaart 400d6a77…), and we don't want flaky tests to depend on operator
# cleanup.
#
# The sweeper surfaces one failure mode only: a parent parked in
# `Awaiting Subtasks` whose last child was deleted. Healthy shapes
# (parent with children, parent in any other column, parent with zero
# children in a non-Awaiting-Subtasks column) are silently omitted.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  DB exists but has no kanban_cards table → exit 0 with a clean
#       report (table-mismatch shouldn't fail the sweep; it just means
#       there's nothing to sweep).
#   4.  clean board — no parked parents at all → report.totals.orphaned_parents == 0, exit 0.
#   5.  parked parent WITH children → not reported.
#   6.  parked parent with ZERO children → reported (the regression).
#   7.  parent in a different column (Backlog) with zero children → not
#       reported (only Awaiting Subtasks is in scope).
#   8.  parent with one child in Done, one in Backlog → not reported
#       (not all children Done, parked-and-waiting is legitimate).
#   9.  --strict with hits → exit 1.
#  10.  real ~/.claude-registry/kanban.db is reachable.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_orphaned_parents_awaiting_subtasks.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with just the kanban_cards table the
# sweeper reads. Column order matches the live schema (PRAGMA
# table_info) so a future operator can paste the CREATE TABLE here from a
# real DB without surprises. Extra columns the sweep doesn't read are
# omitted on purpose — keep the fixture honest about what the script
# depends on.
seed_db() {
  local db="$1"
  rm -f "$db"
  python3 - "$db" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        parent_card_id TEXT,
        title TEXT,
        column TEXT,
        project_key TEXT,
        created_at DATETIME,
        updated_at DATETIME
    );
""")
con.commit(); con.close()
PY
}

# Card insert helper. Args: db id title column parent_card_id
# project_key created_at updated_at. Pass empty string for NULLable
# columns.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, title, col, parent, proj, created, updated = sys.argv[1:9]
def asval(s, none=""):
    return None if s == none else s
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards "
    "(id, title, column, parent_card_id, project_key, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (cid, title, col, asval(parent), asval(proj), asval(created), asval(updated)),
)
con.commit(); con.close()
PY
}

# Run the SUT with KANBAN_DB pointed at the fixture. Extra args are
# forwarded. Captures stdout+stderr merged into one stream; the helper
# echoes the merged stream to its caller.
run() {
  local db="$1"; shift
  KANBAN_DB="$db" python3 "$SUT" "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: --help runs and lists all real flags + synopsis"
out=$(python3 "$SUT" --help 2>&1); rc=$?
check "--help runs without error"     '[ "$rc" -eq 0 ]'
check "--help shows synopsis"         'echo "$out" | grep -qE "^usage:"'
check "--help mentions --json"        'echo "$out" | grep -qE "\-\-json"'
check "--help mentions --db"          'echo "$out" | grep -qE "\-\-db"'
check "--help mentions --strict"      'echo "$out" | grep -qE "\-\-strict"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing DB → exit 2"
out=$(KANBAN_DB="$TMP/does-not-exist.db" python3 "$SUT" 2>&1); rc=$?
check "missing DB → exit 2"           '[ "$rc" -eq 2 ]'
check "missing DB → ERROR on stderr"  'echo "$out" | grep -qE "ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: DB exists but has no kanban_cards table → clean exit, empty report"
nokanban="$TMP/nokanban.db"; : > "$nokanban"
out=$(KANBAN_DB="$nokanban" python3 "$SUT" 2>&1); rc=$?
check "no kanban_cards table → exit 0"   '[ "$rc" -eq 0 ]'
check "no kanban_cards table → empty rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_parents\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 4: clean board — no parked parents at all → empty report"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "C1" "backlog" "Backlog"        ""   "P" "2026-07-01 10:00:00" "2026-07-01 10:00:00"
card "$clean" "C2" "done"    "Done"           ""   "P" "2026-07-02 10:00:00" "2026-07-02 10:00:00"
out=$(run "$clean"); rc=$?
check "clean → exit 0"                '[ "$rc" -eq 0 ]'
check "clean → valid JSON"            'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → totals.orphaned_parents == 0"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_parents\"]==0, d[\"totals\"]"'
check "clean → rows == []"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: parked parent WITH children → not reported"
pc="$TMP/pc.db"; seed_db "$pc"
card "$pc" "PARENT-1" "parked-with-kids"  "Awaiting Subtasks" ""        "P" "2026-07-01 10:00:00" "2026-07-15 10:00:00"
card "$pc" "CHILD-1"  "child-1"           "Done"              "PARENT-1" "P" "2026-07-02 10:00:00" "2026-07-10 10:00:00"
card "$pc" "CHILD-2"  "child-2"           "Done"              "PARENT-1" "P" "2026-07-03 10:00:00" "2026-07-12 10:00:00"
out=$(run "$pc"); rc=$?
check "parked parent with children → exit 0"  '[ "$rc" -eq 0 ]'
check "parked parent with children → 0 rows"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_parents\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 6: parked parent with ZERO children → reported (the regression)"
po="$TMP/po.db"; seed_db "$po"
card "$po" "PARENT-ORPHAN-1" "stranded-parent" "Awaiting Subtasks" "" "P" "2026-07-01 10:00:00" "2026-07-15 10:00:00"
out=$(run "$po"); rc=$?
check "orphan → exit 0 (advisory)"     '[ "$rc" -eq 0 ]'
check "orphan → exactly 1 row"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, len(d[\"rows\"])"'
check "orphan → column is Awaiting Subtasks" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"column\"]==\"Awaiting Subtasks\", d[\"rows\"][0]"'
check "orphan → card_id matches"       'echo "$out" | grep -qF "PARENT-ORPHAN-1"'
check "orphan → reason is human-readable" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"reason\"] and len(r[\"reason\"]) > 20, r"'
check "orphan → parked_since present"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"parked_since\"]==r[\"updated_at\"], r"'

# ----------------------------------------------------------------------------
echo "Task 7: parent in a different column (Backlog) with zero children → not reported"
ncol="$TMP/ncol.db"; seed_db "$ncol"
card "$ncol" "BACKLOG-ORPHAN" "in-backlog" "Backlog" "" "P" "2026-07-01 10:00:00" "2026-07-15 10:00:00"
out=$(run "$ncol"); rc=$?
check "Backlog parent → 0 rows"        'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_parents\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 8: parked parent with one child in Done, one in Backlog → not reported (waiting is legitimate)"
mixed="$TMP/mixed.db"; seed_db "$mixed"
card "$mixed" "PARENT-MIXED"  "parked-mixed"   "Awaiting Subtasks" ""         "P" "2026-07-01 10:00:00" "2026-07-15 10:00:00"
card "$mixed" "CHILD-DONE"    "child-done"     "Done"              "PARENT-MIXED" "P" "2026-07-02 10:00:00" "2026-07-10 10:00:00"
card "$mixed" "CHILD-PENDING" "child-pending"  "Backlog"           "PARENT-MIXED" "P" "2026-07-03 10:00:00" "2026-07-03 10:00:00"
out=$(run "$mixed"); rc=$?
check "mixed-children parent → 0 rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_parents\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 9: --strict with hits → exit 1; clean → exit 0"
out=$(run "$po" --strict); rc=$?
check "strict + hits → exit 1"         '[ "$rc" -eq 1 ]'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"        '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 10: real ~/.claude-registry/kanban.db is reachable and reports JSON"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" python3 "$SUT" 2>&1); rc=$?
  check "real board → exit 0 or 1 (advisory + historic orphans expected)" '[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]'
  check "real board → valid JSON"      'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
