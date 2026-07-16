#!/usr/bin/env bash
# Test harness for scripts/sweep_dangling_plan_refs.py.
#
# Exercises the dangling-plan_ref sweeper against synthetic SQLite fixtures in
# a tempdir, so the tests stay green regardless of the board's real state. The
# real-board check is a final optional task — the production kanban.db may
# legitimately carry historic dangling rows from pre-existing analyst artefacts,
# and we don't want flaky tests to depend on operator cleanup.
#
# The sweep categorises each `plan_ref` row into one of three statuses — the
# same statuses `dispatch._resolve_plan_for_child` returns in
# backend/app/kanban/dispatch.py:1199-1209 — so a hit on the sweeper matches
# the symptom the dispatcher already diagnoses per-session:
#
#   - dangling_parent        parent_card_id doesn't resolve to a kanban_cards row
#   - plan_missing_on_parent parent_card_id resolves, but the referenced
#                            plan_deliverable_id is not on that parent as kind='plan'
#   - malformed_ref          ref is non-empty but not parseable JSON, or missing
#                            one of the two required keys
#
# A healthy plan_ref (everything resolves) is silently omitted from the report.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  error — DB exists but has no kanban_deliverables table → exit 0 with a
#       clean report (table-mismatch shouldn't fail the sweep; it just means
#       there's nothing to sweep).
#   4.  clean board — zero plan_ref rows → report.totals.dangling == 0, exit 0.
#   5.  one dangling_parent row → exactly one row in report, status matches.
#   6.  one plan_missing_on_parent row → exactly one row in report, status matches.
#   7.  one malformed_ref row → exactly one row in report, status matches.
#   8.  healthy plan_ref row → not reported at all.
#   9.  mixed board — one of each kind, plus a healthy row, plus an unrelated
#       kind='note' row; report totals = 3 danglings broken out by status; the
#       healthy row and the unrelated note row are absent from the report.
#  10.  --strict with hits → exit 1.
#  11.  real ~/.claude-registry/kanban.db is reachable.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_dangling_plan_refs.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with just the two tables the sweeper reads.
# Column order matches the live schema (PRAGMA table_info) so a future operator
# can paste the CREATE TABLE here from a real DB without surprises. Extra
# columns the sweep doesn't read are omitted on purpose — keep the fixture
# honest about what the script depends on.
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
        parent_card_id TEXT
    );
    CREATE TABLE kanban_deliverables (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        ref TEXT NOT NULL,
        created_at DATETIME NOT NULL
    );
""")
con.commit(); con.close()
PY
}

# Bare-bones card insert. Args: db id parent_card_id (parent may be empty string).
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, parent = sys.argv[1], sys.argv[2], sys.argv[3]
parent_val = None if parent == "" else parent
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, parent_card_id) VALUES (?, ?)",
    (cid, parent_val),
)
con.commit(); con.close()
PY
}

# Deliverable insert. Args: db id card_id kind ref created_at_iso.
deliv() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, did, cid, kind, ref = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_deliverables (id, card_id, kind, ref, created_at) "
    "VALUES (?, ?, ?, ?, '2026-07-16 10:00:00')",
    (did, cid, kind, ref),
)
con.commit(); con.close()
PY
}

# Run the SUT with KANBAN_DB pointed at the fixture. Extra args are forwarded.
# Echoes stdout+stderr merged to a single stream; captures exit code.
run() {
  local db="$1"; shift
  KANBAN_DB="$db" python3 "$SUT" "$@" 2>&1
}

# Run as a single JSON dump to /dev/stdout regardless of args; lets tests grep
# or jq the output. The merge above means stderr leaks into the same stream —
# fine for tasks where we expect a JSON report on stdout; for tasks that expect
# an error on stderr we use run_err() instead.
run_err() {
  local db="$1"; shift
  local errf="$TMP/err.$$.txt"
  local rc
  KANBAN_DB="$db" python3 "$SUT" "$@" 2>"$errf" 1>/dev/null
  rc=$?
  printf '%s\n' "$(cat "$errf")"
  rm -f "$errf"
  return $rc
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
echo "Task 3: DB exists but has no kanban tables → clean exit, empty report"
nokanban="$TMP/nokanban.db"; : > "$nokanban"  # zero-byte file, no tables
out=$(KANBAN_DB="$nokanban" python3 "$SUT" 2>&1); rc=$?
# Missing tables used to crash with sqlite3.OperationalError; the sweep must
# treat "no tables yet" as "nothing to scan".
check "no kanban tables → exit 0"     '[ "$rc" -eq 0 ]'
check "no kanban tables → empty rows array" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 4: clean board — zero plan_ref rows → empty JSON report"
clean="$TMP/clean.db"; seed_db "$clean"
out=$(run "$clean"); rc=$?
check "clean → exit 0"                '[ "$rc" -eq 0 ]'
check "clean → valid JSON"            'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → totals.dangling == 0"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling\"]==0, d[\"totals\"]"'
check "clean → rows == []"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "clean → by_status all zeros"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"by_status\"]=={\"dangling_parent\":0,\"plan_missing_on_parent\":0,\"malformed_ref\":0}"'

# ----------------------------------------------------------------------------
echo "Task 5: one dangling_parent row"
dp="$TMP/dp.db"; seed_db "$dp"
# child card exists, but parent_card_id in the ref points at a missing row.
card  "$dp" "CHILD-001" ""
deliv "$dp" "DELIV-DP-1" "CHILD-001" "plan_ref" '{"parent_card_id":"PARENT-MISSING","plan_deliverable_id":"PLAN-MISSING"}'
out=$(run "$dp"); rc=$?
check "dangling_parent → exit 0"      '[ "$rc" -eq 0 ]'
check "dangling_parent → exactly 1 row" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, len(d[\"rows\"])"'
check "dangling_parent → status matches" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"dangling_parent\", r"'
check "dangling_parent → exposes parent_id" 'echo "$out" | grep -qF "PARENT-MISSING"'
check "dangling_parent → exposes plan_id"    'echo "$out" | grep -qF "PLAN-MISSING"'

# ----------------------------------------------------------------------------
echo "Task 6: one plan_missing_on_parent row"
pmp="$TMP/pmp.db"; seed_db "$pmp"
card  "$pmp" "PARENT-OK" ""
card  "$pmp" "CHILD-002" "PARENT-OK"
deliv "$pmp" "PLAN-OTHER-1" "PARENT-OK"  "plan" "# only plan on this parent — different id"  # kind=plan but a different id
deliv "$pmp" "DELIV-PMP-1" "CHILD-002" "plan_ref" '{"parent_card_id":"PARENT-OK","plan_deliverable_id":"PLAN-NOT-FOUND"}'
out=$(run "$pmp"); rc=$?
check "plan_missing_on_parent → exit 0"      '[ "$rc" -eq 0 ]'
check "plan_missing_on_parent → status matches" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"plan_missing_on_parent\", r"'
check "plan_missing_on_parent → keeps the unrelated plan out" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"deliverable_id\"] for r in d[\"rows\"]]; assert \"PLAN-OTHER-1\" not in ids, ids"'

# ----------------------------------------------------------------------------
echo "Task 7: one malformed_ref row"
mal="$TMP/mal.db"; seed_db "$mal"
card  "$mal" "CHILD-003" ""
deliv "$mal" "DELIV-MAL-1" "CHILD-003" "plan_ref" 'this is not json'
out=$(run "$mal"); rc=$?
check "malformed_ref → exit 0"        '[ "$rc" -eq 0 ]'
check "malformed_ref → status matches" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"malformed_ref\", r"'

# And a row where JSON parses but one key is missing:
deliv "$mal" "DELIV-MAL-2" "CHILD-003" "plan_ref" '{"parent_card_id":"X"}'  # no plan_deliverable_id
out=$(run "$mal"); rc=$?
check "malformed_ref (missing key) → counted" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"by_status\"][\"malformed_ref\"]==2"'

# ----------------------------------------------------------------------------
echo "Task 8: healthy plan_ref row is not reported"
healthy="$TMP/h.db"; seed_db "$healthy"
card  "$healthy" "PARENT-H" ""
card  "$healthy" "CHILD-H" "PARENT-H"
deliv "$healthy" "PLAN-H"      "PARENT-H" "plan"  "# healthy plan body"
deliv "$healthy" "DELIV-H-1"   "CHILD-H"  "plan_ref" '{"parent_card_id":"PARENT-H","plan_deliverable_id":"PLAN-H"}'
out=$(run "$healthy"); rc=$?
check "healthy → exit 0"             '[ "$rc" -eq 0 ]'
check "healthy → 0 dangling rows"     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling\"]==0, d[\"totals\"]"'
check "healthy → no DELIV-H-1 in output" '! echo "$out" | grep -qF "DELIV-H-1"'

# ----------------------------------------------------------------------------
echo "Task 9: mixed board — 4 plan_refs (3 dangling × 1 each status, 1 healthy) + 1 unrelated kind=note"
mix="$TMP/mix.db"; seed_db "$mix"
# Parent A — fine
card  "$mix" "PA" ""
card  "$mix" "CA" "PA"
deliv "$mix" "PLA"     "PA" "plan" "# fine"
deliv "$mix" "RA"      "CA" "plan_ref" '{"parent_card_id":"PA","plan_deliverable_id":"PLA"}'  # healthy
# Parent B — gone → dangling_parent
card  "$mix" "CB" ""  # no parent card exists with the id referenced below
deliv "$mix" "RB"      "CB" "plan_ref" '{"parent_card_id":"PARENT-GONE","plan_deliverable_id":"PLAN-GONE"}'
# Parent C — alive but wrong plan id → plan_missing_on_parent
card  "$mix" "PC" ""
card  "$mix" "CC" "PC"
deliv "$mix" "PLC"     "PC" "plan" "# unrelated plan"
deliv "$mix" "RC"      "CC" "plan_ref" '{"parent_card_id":"PC","plan_deliverable_id":"PLAN-NOT-ON-C"}'
# Malformed ref
card  "$mix" "CD" ""
deliv "$mix" "RD"      "CD" "plan_ref" '{"oops":true}'
# Unrelated note deliverable (must not appear in the report at all)
deliv "$mix" "NOTE-X"  "CA" "note" "anything"
out=$(run "$mix"); rc=$?
check "mixed → exit 0 (advisory)"    '[ "$rc" -eq 0 ]'
check "mixed → totals.dangling == 3" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"dangling\"]==3, d[\"totals\"]"'
check "mixed → by_status breaks out per category" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"by_status\"]=={\"dangling_parent\":1,\"plan_missing_on_parent\":1,\"malformed_ref\":1}"'
check "mixed → healthy RA absent from rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"deliverable_id\"] for r in d[\"rows\"]]; assert \"RA\" not in ids, ids"'
check "mixed → unrelated NOTE-X absent" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"deliverable_id\"] for r in d[\"rows\"]]; assert \"NOTE-X\" not in ids, ids"'
check "mixed → lengths/title surface human-readable info" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); rs=d[\"rows\"]; assert all(\"reason\" in r and r[\"reason\"] for r in rs), rs[0]"'

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
