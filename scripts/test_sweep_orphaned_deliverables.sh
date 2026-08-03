#!/usr/bin/env bash
# Test harness for scripts/sweep_orphaned_deliverables.py.
#
# Exercises the orphaned-deliverables sweeper against synthetic SQLite
# fixtures in a tempdir, so the tests stay green regardless of the board's
# real state. The real-board check is a final optional task — production
# kanban.db may legitimately carry historic orphans from pre-existing
# sessions, and we don't want flaky tests to depend on operator cleanup.
#
# An "orphaned deliverable" is the exact pathology from kanban card
# 4a60048365004d808e2dbfdd9551afe4 (a4a091fa… was the proof): a card with
# ≥1 deliverable that nevertheless looks dispatchable — not in a terminal
# column, no live claim, no terminal summary — so the orphan-fallback in
# dispatch._next_card reclaims it on the next tick and a fresh session has
# to re-derive the full context to discover that the prior session already
# shipped. The sweep flags every such card so the operator (or a follow-up
# chore card) can move it to Done and stop the silent re-dispatch loop.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  DB exists but has no kanban tables → exit 0 with a clean report.
#   4.  clean board — zero deliverables → totals all zero, exit 0.
#   5.  card with deliverable + non-terminal column + no claim → flagged.
#   6.  card with deliverable + terminal column (Done) → NOT flagged.
#   7.  card with deliverable + terminal column (Impediment) → NOT flagged.
#   8.  card with deliverable + non-terminal column + live claim → NOT flagged.
#   9.  card with deliverable + non-terminal column + no claim but done_summary
#       present → NOT flagged (summary is the enrichment's terminal-move
#       signal — a card with one is already on its way to Done via the
#       op-log even if the move hasn't materialised yet).
#  10.  card with multiple deliverables → still exactly one report row;
#       all deliverables surfaced.
#  11.  mixed board — one orphan + one Done + one live-claimed + one clean →
#       exactly the orphan row in report.
#  12.  the a4a091fa… historical fixture (the card this sweeper was born
#       to catch): fixture has the card in its broken state (column
#       'analyst', deliverable k-spike-transpo-681e, no claim) → flagged.
#      The fix-mirror fixture moves the same card to Done with a Summary
#       comment → NOT flagged. Two fixtures, two opposite verdicts — the
#       assertion is specific, not "passes in both states".
#  13.  --strict with hits → exit 1; --strict clean → exit 0.
#  14.  real ~/.claude-registry/kanban.db is reachable and reports JSON.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_orphaned_deliverables.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with just the two tables the sweeper reads.
# Column order matches the live schema (PRAGMA table_info) so a future
# operator can paste the CREATE TABLE here from a real DB without
# surprises. Extra columns the sweep doesn't read are omitted on purpose
# — keep the fixture honest about what the script depends on.
#
# `done_summary` is materialized from the op-log at request time, not
# stored on the card row (backend/app/kanban/service.py:373
# enrich_done_info scans KanbanOp for `**Summary:** …` comments). The
# fixture therefore needs a kanban_ops row carrying the prefix-comment
# for a "done_summary present" state to be representable here.
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
        claimed_by TEXT
    );
    CREATE TABLE kanban_deliverables (
        id TEXT PRIMARY KEY,
        card_id TEXT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        ref TEXT NOT NULL,
        created_at DATETIME NOT NULL
    );
    CREATE TABLE kanban_ops (
        op_id TEXT PRIMARY KEY,
        device_id TEXT,
        seq INTEGER,
        hlc TEXT,
        project_key TEXT,
        entity_type TEXT,
        entity_id TEXT,
        op_type TEXT,
        payload TEXT,
        created_at DATETIME
    );
""")
con.commit(); con.close()
PY
}

# Card insert. Args: db id column claimed_by project_key title.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, column, claimed_by, pkey, title = sys.argv[1:]
claimed_val = None if claimed_by == "" else claimed_by
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, \"column\", claimed_by) "
    "VALUES (?, ?, ?, ?, ?)",
    (cid, pkey, title, column, claimed_val),
)
con.commit(); con.close()
PY
}

# Deliverable insert. Args: db id card_id kind ref.
deliverable() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, did, card_id, kind, ref = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_deliverables (id, card_id, kind, ref, created_at) "
    "VALUES (?, ?, ?, ?, '2026-07-15T19:49:58')",
    (did, card_id, kind, ref),
)
con.commit(); con.close()
PY
}

# Summary-comment op insert. Args: db card_id summary_text.
summary_comment() {
  python3 - "$@" <<'PY'
import sqlite3, sys, json
db, cid, summary = sys.argv[1:]
import uuid
op_id = uuid.uuid4().hex
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_ops (op_id, device_id, seq, hlc, project_key, "
    "entity_type, entity_id, op_type, payload, created_at) "
    "VALUES (?, 'device-test', 1, '2026-07-15T20:00:00.000Z', 'proj', "
    "'comment', ?, 'comment', ?, '2026-07-15T20:00:00')",
    (op_id, cid, json.dumps({"text": f"**Summary:** {summary}"})),
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
echo "Task 3: DB exists but has no kanban tables → clean exit, empty report"
nokanban="$TMP/nokanban.db"; : > "$nokanban"  # zero-byte file, no tables
out=$(KANBAN_DB="$nokanban" python3 "$SUT" 2>&1); rc=$?
check "no kanban tables → exit 0"     '[ "$rc" -eq 0 ]'
check "no kanban tables → empty rows array" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 4: clean board — zero deliverables → empty JSON report"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "A" "Backlog" "" "proj" "no deliverable"
out=$(run "$clean"); rc=$?
check "clean → exit 0"                '[ "$rc" -eq 0 ]'
check "clean → valid JSON"            'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → orphaned_cards == 0"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "clean → rows == []"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: card with deliverable + non-terminal column + no claim → flagged"
orphan="$TMP/orphan.db"; seed_db "$orphan"
card  "$orphan" "ORPHAN-1" "analyst" "" "proj" "orphan card"
deliverable "$orphan" "del-1" "ORPHAN-1" "branch" "k-spike-test-0001"
out=$(run "$orphan"); rc=$?
check "orphan → exit 0"               '[ "$rc" -eq 0 ]'
check "orphan → exactly 1 row"        'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "orphan → surfaces card id"     'echo "$out" | grep -qF "ORPHAN-1"'
check "orphan → surfaces title"       'echo "$out" | grep -qF "orphan card"'
check "orphan → surfaces ref"         'echo "$out" | grep -qF "k-spike-test-0001"'
check "orphan → column == analyst"    'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"column\"]==\"analyst\", d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 6: card with deliverable + terminal column (Done) → NOT flagged"
done_db="$TMP/done.db"; seed_db "$done_db"
card  "$done_db" "DONE-1" "Done" "" "proj" "finished card"
deliverable "$done_db" "del-2" "DONE-1" "branch" "k-finished-0001"
out=$(run "$done_db"); rc=$?
check "Done → exit 0"                 '[ "$rc" -eq 0 ]'
check "Done → 0 orphaned"             'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "Done → not reported"           '! echo "$out" | grep -qF "DONE-1"'

# ----------------------------------------------------------------------------
echo "Task 7: card with deliverable + terminal column (Impediment) → NOT flagged"
imp_db="$TMP/imp.db"; seed_db "$imp_db"
card  "$imp_db" "IMP-1" "Impediment" "" "proj" "blocked card"
deliverable "$imp_db" "del-3" "IMP-1" "note" "blocked-on-something"
out=$(run "$imp_db"); rc=$?
check "Impediment → 0 orphaned"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "Impediment → not reported"     '! echo "$out" | grep -qF "IMP-1"'

# ----------------------------------------------------------------------------
echo "Task 8: card with deliverable + non-terminal column + live claim → NOT flagged"
live="$TMP/live.db"; seed_db "$live"
card  "$live" "LIVE-1" "analyst" "agent-foo" "proj" "claimed card"
deliverable "$live" "del-4" "LIVE-1" "branch" "k-live-0001"
out=$(run "$live"); rc=$?
check "live-claim → 0 orphaned"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "live-claim → not reported"     '! echo "$out" | grep -qF "LIVE-1"'

# ----------------------------------------------------------------------------
echo "Task 9: card with done_summary present (even on non-terminal column) → NOT flagged"
summ="$TMP/summ.db"; seed_db "$summ"
card  "$summ" "SUMM-1" "analyst" "" "proj" "summary-only card"
deliverable "$summ" "del-5" "SUMM-1" "branch" "k-summary-0001"
summary_comment "$summ" "SUMM-1" "Did the thing"
out=$(run "$summ"); rc=$?
check "summary → 0 orphaned"          'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "summary → not reported"        '! echo "$out" | grep -qF "SUMM-1"'

# ----------------------------------------------------------------------------
echo "Task 10: card with multiple deliverables → one report row, all surfaced"
multi="$TMP/multi.db"; seed_db "$multi"
card  "$multi" "MULTI-1" "analyst" "" "proj" "multi-deliverable card"
deliverable "$multi" "del-6a" "MULTI-1" "branch" "k-multi-0001-branch"
deliverable "$multi" "del-6b" "MULTI-1" "commit" "abc1234"
out=$(run "$multi"); rc=$?
check "multi → exactly 1 row"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "multi → surfaces branch ref"   'echo "$out" | grep -qF "k-multi-0001-branch"'
check "multi → surfaces commit ref"   'echo "$out" | grep -qF "abc1234"'
check "multi → deliverable count == 2" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"][0][\"deliverables\"])==2, d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 11: mixed board — exactly the orphan surfaces"
mix="$TMP/mix.db"; seed_db "$mix"
card  "$mix" "CLEAN-1"  "Backlog"    ""            "proj" "no deliverable"
card  "$mix" "DONE-1"   "Done"       ""            "proj" "finished"
deliverable "$mix" "del-d1" "DONE-1" "branch" "k-done-0001"
card  "$mix" "LIVE-1"   "analyst"    "agent-foo"   "proj" "claimed"
deliverable "$mix" "del-l1" "LIVE-1" "branch" "k-live-0001"
card  "$mix" "ORPHAN-1" "analyst"    ""            "proj" "the real orphan"
deliverable "$mix" "del-o1" "ORPHAN-1" "branch" "k-orphan-0001"
out=$(run "$mix"); rc=$?
check "mixed → exit 0"                '[ "$rc" -eq 0 ]'
check "mixed → orphaned_cards == 1"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==1, d[\"totals\"]"'
check "mixed → only ORPHAN-1 surfaced" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=[r[\"card_id\"] for r in d[\"rows\"]]; assert ids==[\"ORPHAN-1\"], ids"'

# ----------------------------------------------------------------------------
echo "Task 12: a4a091fa… historical fixture — broken state flagged, fixed state not"
# Broken state: column='analyst', deliverable k-spike-transpo-681e, no claim.
# This is the exact shape the kanban card 4a60048365004d808e2dbfdd9551afe4
# reported: a session shipped a deliverable, never moved the card to Done,
# the dispatcher's orphan-fallback reclaimed it 13 days later.
broken="$TMP/a4-broken.db"; seed_db "$broken"
card  "$broken" "a4a091fa-test" "analyst" "" "proj" "spike doc"
deliverable "$broken" "del-a4" "a4a091fa-test" "branch" "k-spike-transpo-681e"
out=$(run "$broken"); rc=$?
check "broken-state → flagged"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==1, d[\"totals\"]"'
check "broken-state → surfaces a4 id" 'echo "$out" | grep -qF "a4a091fa-test"'
check "broken-state → surfaces ref"   'echo "$out" | grep -qF "k-spike-transpo-681e"'

# Fixed state: same card moved to Done with a Summary comment — the
# assertion is specific, NOT "passes in both states". A naive predicate
# like "has deliverable" would also accept the fixed state; this test
# specifically rejects it.
fixed="$TMP/a4-fixed.db"; seed_db "$fixed"
card  "$fixed" "a4a091fa-test" "Done" "" "proj" "spike doc"
deliverable "$fixed" "del-a4" "a4a091fa-test" "branch" "k-spike-transpo-681e"
summary_comment "$fixed" "a4a091fa-test" "Spike completed; no follow-up needed."
out=$(run "$fixed"); rc=$?
check "fixed-state → 0 orphaned"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"orphaned_cards\"]==0, d[\"totals\"]"'
check "fixed-state → not reported"    '! echo "$out" | grep -qF "a4a091fa-test"'

# ----------------------------------------------------------------------------
echo "Task 13: --strict with hits → exit 1; clean → exit 0"
out=$(run "$orphan" --strict); rc=$?
check "strict + hits → exit 1"        '[ "$rc" -eq 1 ]'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"       '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 14: real ~/.claude-registry/kanban.db is reachable and reports JSON"
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