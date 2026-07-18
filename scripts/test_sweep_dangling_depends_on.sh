#!/usr/bin/env bash
# Test harness for scripts/sweep_dangling_depends_on.py.
#
# Exercises the dangling-depends_on sweeper against synthetic SQLite fixtures in
# a tempdir, so the tests stay green regardless of the board's real state. The
# real-board check is a final optional task — the production kanban.db may
# legitimately carry historic dangling deps from pre-existing deleted parents,
# and we don't want flaky tests to depend on operator cleanup.
#
# The sweep flags every non-Done card whose `depends_on` names a card id that
# does not resolve to any card in the DB (existence is board-wide, not
# per-project). A card whose every dep resolves is silently omitted; a card in
# the 'Done' column is skipped entirely (it never dispatches, so a dangling dep
# on it is harmless).
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  error — DB exists but has no kanban_cards table → exit 0 with a clean
#       report (table-mismatch shouldn't fail the sweep; nothing to sweep).
#   4.  clean board — zero cards with depends_on → totals all zero, exit 0.
#   5.  one card with a dangling dep → exactly one row, dep-id surfaced.
#   6.  a healthy card (dep resolves) → not reported at all.
#   7.  a Done card with a dangling dep → not reported (Done is skipped).
#   8.  a card with a mix of one dangling + one healthy dep → reported, only
#       the dangling id appears in dangling_dep_ids, both in depends_on.
#   9.  mixed board — one dangling, one healthy, one Done-with-dangling; totals
#       count only the single live dangling card.
#  10.  --strict with hits → exit 1; --strict clean → exit 0.
#  11.  real ~/.claude-registry/kanban.db is reachable and reports JSON.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_dangling_depends_on.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with just the one table the sweeper reads.
# Column order matches the live schema (a subset of kanban_cards) so a future
# operator can paste the CREATE TABLE here from a real DB without surprises.
# Extra columns the sweep doesn't read are omitted on purpose — keep the
# fixture honest about what the script depends on. `column` is a SQLite
# keyword, so it is quoted here and in the SUT's query.
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
        project_key TEXT,
        title TEXT,
        "column" TEXT NOT NULL DEFAULT 'Backlog',
        depends_on TEXT
    );
""")
con.commit(); con.close()
PY
}

# Card insert. Args: db id column depends_on_json project_key title.
# depends_on may be empty string (stored as NULL) or a JSON array string.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, column, deps, pkey, title = sys.argv[1:]
deps_val = None if deps == "" else deps
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, \"column\", depends_on) "
    "VALUES (?, ?, ?, ?, ?)",
    (cid, pkey, title, column, deps_val),
)
con.commit(); con.close()
PY
}

# Run the SUT with KANBAN_DB pointed at the fixture. Extra args are forwarded.
# Echoes stdout+stderr merged; captures exit code by the caller via $?.
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
nokanban="$TMP/nokanban.db"; : > "$nokanban"  # zero-byte file, no tables
out=$(KANBAN_DB="$nokanban" python3 "$SUT" 2>&1); rc=$?
check "no kanban tables → exit 0"     '[ "$rc" -eq 0 ]'
check "no kanban tables → empty rows array" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 4: clean board — zero cards with depends_on → empty JSON report"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "A" "Backlog" "" "proj" "no deps card"
out=$(run "$clean"); rc=$?
check "clean → exit 0"                '[ "$rc" -eq 0 ]'
check "clean → valid JSON"            'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → dangling_dep_ids == 0" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==0, d[\"totals\"]"'
check "clean → rows == []"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: one card with a dangling dep"
dp="$TMP/dp.db"; seed_db "$dp"
card "$dp" "CHILD-001" "Backlog" '["PARENT-MISSING"]' "proj" "orphan child"
out=$(run "$dp"); rc=$?
check "dangling → exit 0"             '[ "$rc" -eq 0 ]'
check "dangling → exactly 1 row"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, len(d[\"rows\"])"'
check "dangling → surfaces card id"   'echo "$out" | grep -qF "CHILD-001"'
check "dangling → surfaces title"     'echo "$out" | grep -qF "orphan child"'
check "dangling → surfaces dep id"    'echo "$out" | grep -qF "PARENT-MISSING"'
check "dangling → dep in dangling_dep_ids" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"dangling_dep_ids\"]==[\"PARENT-MISSING\"], d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 6: healthy card (dep resolves) is not reported"
h="$TMP/h.db"; seed_db "$h"
card "$h" "PARENT-OK" "Done"    ""              "proj" "the parent"
card "$h" "CHILD-OK"  "Backlog" '["PARENT-OK"]' "proj" "healthy child"
out=$(run "$h"); rc=$?
check "healthy → exit 0"              '[ "$rc" -eq 0 ]'
check "healthy → 0 dangling"          'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==0, d[\"totals\"]"'
check "healthy → child absent"        '! echo "$out" | grep -qF "CHILD-OK"'
check "healthy → counted in cards_with_depends_on" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"cards_with_depends_on\"]==1, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 7: a Done card with a dangling dep is skipped"
dn="$TMP/dn.db"; seed_db "$dn"
card "$dn" "DONE-CARD" "Done" '["GONE"]' "proj" "already done"
out=$(run "$dn"); rc=$?
check "Done card → 0 dangling"        'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==0, d[\"totals\"]"'
check "Done card → not reported"      '! echo "$out" | grep -qF "DONE-CARD"'

# ----------------------------------------------------------------------------
echo "Task 8: mix of one dangling + one healthy dep on one card"
mx1="$TMP/mx1.db"; seed_db "$mx1"
card "$mx1" "PARENT-LIVE" "Done"    ""                            "proj" "live parent"
card "$mx1" "MIXED-CHILD" "Backlog" '["PARENT-LIVE","GONE-DEP"]'  "proj" "mixed child"
out=$(run "$mx1"); rc=$?
check "mix → exactly 1 row"           'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "mix → only dangling id flagged" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"dangling_dep_ids\"]==[\"GONE-DEP\"], d[\"rows\"][0]"'
check "mix → depends_on keeps both"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"depends_on\"]==[\"PARENT-LIVE\",\"GONE-DEP\"], d[\"rows\"][0]"'
check "mix → dangling_dep_ids total 1" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==1, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 9: mixed board — dangling + healthy + Done-with-dangling"
mix="$TMP/mix.db"; seed_db "$mix"
card "$mix" "P-LIVE"  "Done"    ""             "proj" "live parent"
card "$mix" "C-OK"    "Backlog" '["P-LIVE"]'   "proj" "healthy child"
card "$mix" "C-BAD"   "Backlog" '["P-GONE"]'   "proj" "orphan child"
card "$mix" "C-DONE"  "Done"    '["P-GONE"]'   "proj" "done with dangling"
out=$(run "$mix"); rc=$?
check "mixed → exit 0 (advisory)"    '[ "$rc" -eq 0 ]'
check "mixed → cards_with_dangling == 1" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"cards_with_dangling\"]==1, d[\"totals\"]"'
check "mixed → dangling_dep_ids == 1" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling_dep_ids\"]==1, d[\"totals\"]"'
check "mixed → only C-BAD reported"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"card_id\"] for r in d[\"rows\"]]; assert ids==[\"C-BAD\"], ids"'
check "mixed → healthy C-OK absent"  '! echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"card_id\"] for r in d[\"rows\"]]; print(\"C-OK\" in ids)" | grep -q True'
check "mixed → Done C-DONE absent"   '! echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"card_id\"] for r in d[\"rows\"]]; print(\"C-DONE\" in ids)" | grep -q True'

# ----------------------------------------------------------------------------
echo "Task 10: --strict with hits → exit 1; clean → exit 0"
out=$(run "$dp" --strict); rc=$?
check "strict + hits → exit 1"       '[ "$rc" -eq 1 ]'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"      '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 11: real ~/.claude-registry/kanban.db is reachable and reports JSON"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" python3 "$SUT" 2>&1); rc=$?
  check "real board → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
  check "real board → valid JSON"      'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
