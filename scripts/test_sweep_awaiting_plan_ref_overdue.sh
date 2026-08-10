#!/usr/bin/env bash
# Test harness for scripts/sweep_awaiting_plan_ref_overdue.py.
#
# Exercises the awaiting-plan_ref-overdue sweeper against synthetic SQLite
# fixtures in a tempdir, so the tests stay green regardless of the board's
# real state. The real-board check is a final optional task — the production
# kanban.db may legitimately carry historic overdue rows from before the
# inline fix (kaart 2341a40e…), and we don't want flaky tests to depend on
# operator cleanup.
#
# The sweeper surfaces one failure mode only: a card whose
# ``held_reason='awaiting_plan_ref'`` is older than the deadline
# (``dep_resolver.PLAN_REF_DEADLINE_SECONDS`` = 600s). Healthy shapes
# (different hold reason, awaiting_plan_ref within the deadline, card
# with no held_reason) are silently omitted.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  DB exists but has no kanban_cards table → exit 0 with a clean
#       report (table-mismatch shouldn't fail the sweep; it just means
#       there's nothing to sweep).
#   4.  clean board — no parked children at all → clean report.
#   5.  child with awaiting_plan_ref stamp within the deadline → not reported.
#   6.  child with awaiting_plan_ref stamp older than the deadline → reported.
#   7.  card with a different hold reason → not reported.
#   8.  card with no held_reason at all → not reported.
#   9.  --strict with hits → exit 1.
#  10.  real ~/.claude-registry/kanban.db is reachable.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_awaiting_plan_ref_overdue.py"

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
        metadata TEXT,
        held_reason TEXT,
        held_since DATETIME,
        created_at DATETIME,
        updated_at DATETIME
    );
""")
con.commit(); con.close()
PY
}

# Card insert helper. Args: db id title column parent_card_id
# project_key metadata held_reason held_since created_at updated_at.
# Pass empty string for NULLable columns.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, title, col, parent, proj, meta, held_reason, held_since, created, updated = sys.argv[1:12]
def asval(s, none=""):
    return None if s == none else s
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards "
    "(id, title, column, parent_card_id, project_key, metadata, "
    " held_reason, held_since, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (cid, title, col, asval(parent), asval(proj), asval(meta),
     asval(held_reason), asval(held_since), asval(created), asval(updated)),
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
check "--help mentions the deadline"  'echo "$out" | grep -qE "deadline"'

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
check "no kanban_cards table → empty rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"overdue\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 4: clean board — no parked children at all → empty report"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "C1" "backlog"  "Backlog" "" "P" "" "" "" "2026-08-10 10:00:00" "2026-08-10 10:00:00"
card "$clean" "C2" "done"     "Done"    "" "P" "" "" "" "2026-08-09 10:00:00" "2026-08-09 10:00:00"
out=$(run "$clean"); rc=$?
check "clean → exit 0"               '[ "$rc" -eq 0 ]'
check "clean → valid JSON"           'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → totals.overdue == 0"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"overdue\"]==0, d[\"totals\"]"'
check "clean → rows == []"           'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: child with awaiting_plan_ref stamp within the deadline → not reported"
fresh="$TMP/fresh.db"; seed_db "$fresh"
# held_since is "now minus 30 seconds" — comfortably under the 600s deadline.
RECENT=$(python3 -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)).isoformat(timespec='seconds'))")
card "$fresh" "FRESH-1" "fresh-hold" "Backlog" "PARENT-1" "P" "" "awaiting_plan_ref" "$RECENT" "2026-08-10 10:00:00" "2026-08-10 10:00:00"
out=$(run "$fresh"); rc=$?
check "fresh hold → exit 0"          '[ "$rc" -eq 0 ]'
check "fresh hold → 0 rows"          'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"overdue\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 6: child with awaiting_plan_ref stamp older than the deadline → reported"
overdue="$TMP/overdue.db"; seed_db "$overdue"
STALE=$(python3 -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=16)).isoformat(timespec='seconds'))")
card "$overdue" "STUCK-1" "stuck-16-days" "Backlog" "PARENT-1" "P" "" "awaiting_plan_ref" "$STALE" "2026-07-25 10:00:00" "2026-07-25 10:00:00"
out=$(run "$overdue"); rc=$?
check "overdue → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
check "overdue → exactly 1 row"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, len(d[\"rows\"])"'
check "overdue → card_id matches"    'echo "$out" | grep -qF "STUCK-1"'
check "overdue → has parent_card_id" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"parent_card_id\"]==\"PARENT-1\", d[\"rows\"][0]"'
check "overdue → overdue_seconds > 600" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"overdue_seconds\"] > 600, d[\"rows\"][0]"'
check "overdue → reason is human-readable" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"reason\"] and len(r[\"reason\"]) > 20, r"'
check "overdue → has_marker=False when no marker" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"has_marker\"] is False, d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 7: card with a different hold reason → not reported"
other="$TMP/other.db"; seed_db "$other"
card "$other" "OTHER-1" "dependent-hold" "Backlog" "" "P" "" "dependent" "$STALE" "2026-07-25 10:00:00" "2026-07-25 10:00:00"
out=$(run "$other"); rc=$?
check "non-awaiting hold → 0 rows"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"overdue\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 8: card with no held_reason at all → not reported"
nohold="$TMP/nohold.db"; seed_db "$nohold"
card "$nohold" "NONE-1" "no-hold" "Backlog" "" "P" "" "" "" "2026-07-25 10:00:00" "2026-07-25 10:00:00"
out=$(run "$nohold"); rc=$?
check "no hold → 0 rows"             'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"overdue\"]==0, d[\"totals\"]; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 9: --strict with hits → exit 1; clean → exit 0"
out=$(run "$overdue" --strict); rc=$?
check "strict + hits → exit 1"       '[ "$rc" -eq 1 ]'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"      '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 10: real ~/.claude-registry/kanban.db is reachable and reports JSON"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" python3 "$SUT" 2>&1); rc=$?
  check "real board → exit 0 or 1 (advisory + historic sticks expected)" '[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]'
  check "real board → valid JSON"      'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
