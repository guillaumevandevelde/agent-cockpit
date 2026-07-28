#!/usr/bin/env bash
# Test harness for scripts/check-analysis-outcomes.sh.
#
# Exercises the outcome-evidence sweeper against synthetic SQLite fixtures in
# a tempdir, so the tests stay green regardless of the board's real state
# (the production kanban.db may legitimately contain historic Done-analyses
# pre-dating the gate — those would mask the signal the tests are checking).
#
# Tasks covered:
#   1.  arg parsing — `--help` works and mentions the four real flags.
#   2.  clean case — every Done analysis carries an outcome witness → exit 0
#       and "OK".
#   3.  bare Done analysis (work_type='analysis') → hit, reports all three
#       missing witnesses (outcome-comment, label, children).
#   4.  analysis with child card (parent_card_id) → NOT a hit, even though
#       label + comment are missing.
#   5.  analysis with `**Outcome:**` comment → NOT a hit, even without label
#       or children.
#   6.  analysis with `not-feasible` label → NOT a hit.
#   7.  analysis with `no-action-needed` label → NOT a hit.
#   8.  non-analysis card (feature / engineer) on Done → never a hit.
#   9.  agent='analyst' is a sufficient hit-trigger even with work_type=NULL.
#  10.  historic vs. new split: a card created before `--since` is reported
#       as historic; a card on/after the threshold is reported as NEW.
#  11.  error path — missing DB → exit 2.
#  12.  error path — bad --since format → exit 2.
#  13.  --strict mode → exit 1 on hits; exit 0 when clean.
#  14.  unknown argument → exit 2.
#  15.  inline Python query failure → exit 2 + wrapper ERROR and captured
#       sqlite diagnostic.
#  16.  real ~/.claude-registry/kanban.db is reachable AND the real board
#       emits the clean-state OK line (not the loose "OK or WARNING"
#       tautology that an earlier shape of this task masked — see
#       self-improve card e5136a3f959d4886a7757b85e9d31f55).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/check-analysis-outcomes.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---
# Fixture: a minimal kanban DB matching the real schema's column order. Only
# the columns the script actually reads matter, but keeping them in order
# avoids confusion when comparing against docs/cockpit/kanban-models.md.
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
        description TEXT,
        column TEXT,
        rank TEXT,
        priority TEXT,
        labels JSON,
        agent TEXT,
        transport TEXT,
        claimed_by TEXT,
        claimed_at DATETIME,
        created_at DATETIME,
        updated_at DATETIME,
        title_hlc TEXT,
        description_hlc TEXT,
        column_hlc TEXT,
        rank_hlc TEXT,
        claim_hlc TEXT,
        resume_session_id TEXT,
        resume_project_folder TEXT,
        scheduled_at TEXT,
        dispatch_failures INTEGER DEFAULT 0,
        analyst_agent_id TEXT,
        executor_agent_id TEXT,
        parent_card_id TEXT,
        analyst_run_id TEXT,
        depends_on TEXT,
        work_type TEXT,
        metadata TEXT,
        model TEXT,
        column_overrides TEXT
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
        payload JSON,
        created_at DATETIME
    );
""")
con.commit(); con.close()
PY
}

# Insert one card. Args: db, id, title, col, labels_json, work_type, agent,
# parent_card_id, created_at. created_at is "YYYY-MM-DD HH:MM:SS".
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, title, col, labels, work_type, agent, parent, created = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    """INSERT INTO kanban_cards VALUES
        (?, 'proj', ?, '', ?, '', NULL, ?, ?, NULL, NULL, NULL, ?, ?,
         NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL, ?,
         NULL, NULL, ?, NULL, NULL, NULL)
    """,
    (cid, title, col, labels, agent, created, created, parent, work_type),
)
con.commit(); con.close()
PY
}

# Add an op row. Args: db, op_id, entity_id, op_type, payload_json, created_at.
op() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, oid, eid, otype, payload, created = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    """INSERT INTO kanban_ops VALUES
        (?, 'dev', 1, 'hlc', 'proj', 'comment', ?, ?, ?, ?)""",
    (oid, eid, otype, payload, created),
)
con.commit(); con.close()
PY
}

# Run the SUT with KANBAN_DB pointed at the fixture. Extra args go on the
# command line. Echoes stdout+stderr, captures exit code.
run() {
  local db="$1"; shift
  KANBAN_DB="$db" bash "$SUT" "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help works and lists the four real flags"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help runs without error" 'echo "$out" | grep -qE "check-analysis-outcomes.sh"'
check "--help mentions --strict"    'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --since"     'echo "$out" | grep -qE "\-\-since"'
check "--help mentions --db"        'echo "$out" | grep -qE "\-\-db"'

# ----------------------------------------------------------------------------
echo "Task 2: clean board — every Done analysis has a witness"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "ALL00001" "Analysis with comment" "Done"  "null"                "analysis" "engineer" "" "2026-07-10 10:00:00"
op    "$clean" "op1"     "ALL00001" "comment" '{"text":"**Outcome:** decomposed — split into 3 follow-ups"}' "2026-07-10 10:00:00"
card "$clean" "ALL00002" "Analysis with child"    "Done"  "null"                "analysis" "engineer" "" "2026-07-10 10:00:00"
card "$clean" "CHILD02A" "  child"                "Backlog" "null"              "feature"  "engineer" "ALL00002" "2026-07-10 10:01:00"
card "$clean" "ALL00003" "Analysis not-feasible"  "Done"  '["not-feasible"]'    "analysis" "engineer" "" "2026-07-10 10:00:00"
card "$clean" "ALL00004" "Analysis no-action"     "Done"  '["no-action-needed"]' "analysis" "engineer" "" "2026-07-10 10:00:00"
out=$(run "$clean"); rc=$?
check "clean → exit 0"               '[ "$rc" -eq 0 ]'
check "clean → prints OK"            'echo "$out" | grep -qE "^OK:"'
check "clean → does NOT print WARNING" '! echo "$out" | grep -qE "WARNING:"'
out=$(run "$clean" --strict); rc=$?
check "clean + --strict → exit 0"    '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 3: bare Done analysis (work_type='analysis') → hit, all 3 missing"
bare="$TMP/bare.db"; seed_db "$bare"
card "$bare" "BARE0001" "Bare historic analysis" "Done" "null" "analysis" "engineer" "" "2026-07-10 10:00:00"
out=$(run "$bare"); rc=$?
check "bare → exit 0 (advisory)"     '[ "$rc" -eq 0 ]'
check "bare → WARNING header"        'echo "$out" | grep -qE "WARNING:.*without outcome evidence"'
check "bare → names the card"        'echo "$out" | grep -qF "BARE0001"'
check "bare → reports outcome-comment" 'echo "$out" | grep -qF "outcome-comment"'
check "bare → reports label"         'echo "$out" | grep -qF "label"'
check "bare → reports children"      'echo "$out" | grep -qF "children"'
out=$(run "$bare" --strict); rc=$?
check "bare + --strict → exit 1"     '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 4: analysis with ≥1 child card → NOT a hit"
kid="$TMP/kid.db"; seed_db "$kid"
card "$kid" "KID00001" "Analysis with children" "Done"    "null" "analysis" "engineer" ""      "2026-07-10 10:00:00"
card "$kid" "KID00002" "  child A"              "Backlog" "null" "feature"  "engineer" "KID00001" "2026-07-10 10:01:00"
out=$(run "$kid"); rc=$?
check "kid → exit 0 (clean)"         '[ "$rc" -eq 0 ]'
check "kid → prints OK"              'echo "$out" | grep -qE "^OK:"'
check "kid → does NOT name the parent" '! echo "$out" | grep -qF "KID00001"'

# ----------------------------------------------------------------------------
echo "Task 5: analysis with **Outcome:** comment → NOT a hit"
cmt="$TMP/cmt.db"; seed_db "$cmt"
card "$cmt" "CMT00001" "Analysis with comment" "Done" "null" "analysis" "engineer" "" "2026-07-10 10:00:00"
op    "$cmt" "op-cmt1" "CMT00001" "comment" '{"text":"**Outcome:** no_action_needed — strategic doc only"}' "2026-07-10 10:00:00"
out=$(run "$cmt"); rc=$?
check "cmt → exit 0"                 '[ "$rc" -eq 0 ]'
check "cmt → prints OK"              'echo "$out" | grep -qE "^OK:"'
check "cmt → does NOT name the card" '! echo "$out" | grep -qF "CMT00001"'

# ----------------------------------------------------------------------------
echo "Task 6: analysis with not-feasible label → NOT a hit"
nf="$TMP/nf.db"; seed_db "$nf"
card "$nf" "NF000001" "Analysis marked not-feasible" "Done" '["not-feasible"]' "analysis" "engineer" "" "2026-07-10 10:00:00"
out=$(run "$nf"); rc=$?
check "nf → exit 0"                  '[ "$rc" -eq 0 ]'
check "nf → prints OK"               'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 7: analysis with no-action-needed label → NOT a hit"
nok="$TMP/nok.db"; seed_db "$nok"
card "$nok" "NOK00001" "Analysis marked no-action" "Done" '["no-action-needed"]' "analysis" "engineer" "" "2026-07-10 10:00:00"
out=$(run "$nok"); rc=$?
check "nok → exit 0"                 '[ "$rc" -eq 0 ]'
check "nok → prints OK"              'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 8: non-analysis card on Done → never a hit (even bare)"
nona="$TMP/nona.db"; seed_db "$nona"
card "$nona" "FEAT001" "Bare feature Done" "Done" "null" "feature" "engineer" "" "2026-07-10 10:00:00"
card "$nona" "CHOR001" "Bare chore Done"   "Done" "null" "chore"   "engineer" "" "2026-07-10 10:00:00"
card "$nona" "BUGG001" "Bare bug Done"     "Done" "null" "bug"     "engineer" "" "2026-07-10 10:00:00"
card "$nona" "NONE001" "Bare no-type Done" "Done" "null" ""        "engineer" "" "2026-07-10 10:00:00"
out=$(run "$nona"); rc=$?
check "nona → exit 0"                '[ "$rc" -eq 0 ]'
check "nona → prints OK"             'echo "$out" | grep -qE "^OK:"'
check "nona → names none of the bare cards" '
  ! echo "$out" | grep -qF "FEAT001" &&
  ! echo "$out" | grep -qF "CHOR001" &&
  ! echo "$out" | grep -qF "BUGG001" &&
  ! echo "$out" | grep -qF "NONE001"
'

# ----------------------------------------------------------------------------
echo "Task 9: agent='analyst' triggers a hit even with work_type=NULL"
agent="$TMP/agent.db"; seed_db "$agent"
card "$agent" "AGENT001" "Analyst-agent bare" "Done" "null" "" "analyst" "" "2026-07-10 10:00:00"
out=$(run "$agent"); rc=$?
check "agent → exit 0 (advisory)"     '[ "$rc" -eq 0 ]'
check "agent → names the card"       'echo "$out" | grep -qF "AGENT001"'

# ----------------------------------------------------------------------------
echo "Task 10: historic vs. new split"
split="$TMP/split.db"; seed_db "$split"
# HISTORIC: created 2026-07-10, threshold 2026-07-16 → historic
card "$split" "HIST0001" "Historic offender"  "Done" "null" "analysis" "engineer" "" "2026-07-10 10:00:00"
# NEW: created 2026-07-17, threshold 2026-07-16 → NEW
card "$split" "NEW00001" "New offender"       "Done" "null" "analysis" "engineer" "" "2026-07-17 10:00:00"
out=$(run "$split" --since=2026-07-16); rc=$?
check "split → exit 0 (advisory)"    '[ "$rc" -eq 0 ]'
check "split → marks HIST0001 as historic" 'echo "$out" | grep -qF "historic" && echo "$out" | grep -qF "HIST0001"'
check "split → marks NEW00001 as NEW"     'echo "$out" | grep -qF "NEW    " && echo "$out" | grep -qF "NEW00001"'
check "split → counts both buckets"  'echo "$out" | grep -qE "1 since 2026-07-16, 1 historic"'

# Edge: card created exactly on the threshold is NOT historic
card "$split" "EDGE0001" "On-threshold" "Done" "null" "analysis" "engineer" "" "2026-07-16 10:00:00"
out=$(run "$split" --since=2026-07-16); rc=$?
check "split → EDGE0001 is NEW (not historic)" '
  echo "$out" | grep -qF "EDGE0001" &&
  ! echo "$out" | grep -qE "\\[historic\\] EDGE0001"
'

# ----------------------------------------------------------------------------
echo "Task 11: error path — missing DB → exit 2"
out=$(KANBAN_DB="$TMP/does-not-exist.db" bash "$SUT" 2>&1); rc=$?
check "missing DB → exit 2"          '[ "$rc" -eq 2 ]'
check "missing DB → ERROR mentions path" 'echo "$out" | grep -qE "ERROR.*kanban DB"'

# ----------------------------------------------------------------------------
echo "Task 12: error path — bad --since format → exit 2"
out=$(KANBAN_DB="$clean" bash "$SUT" --since=not-a-date 2>&1); rc=$?
check "bad --since → exit 2"         '[ "$rc" -eq 2 ]'
check "bad --since → ERROR mentions YYYY-MM-DD" 'echo "$out" | grep -qE "ERROR.*YYYY-MM-DD"'

# ----------------------------------------------------------------------------
echo "Task 13: --strict mode round-trip"
out=$(run "$bare" --strict); rc=$?
check "strict + hits → exit 1"       '[ "$rc" -eq 1 ]'
check "strict + hits → still names the card" 'echo "$out" | grep -qF "BARE0001"'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"      '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 14: unknown argument → exit 2"
out=$(KANBAN_DB="$clean" bash "$SUT" --bogus 2>&1); rc=$?
check "unknown arg → exit 2"         '[ "$rc" -eq 2 ]'
check "unknown arg → ERROR names the bad flag" 'echo "$out" | grep -qF "unknown argument"'

# ----------------------------------------------------------------------------
echo "Task 15: inline Python query failure prints wrapper + captured stderr"
bad_db="$TMP/bad.db"
python3 - "$bad_db" <<'PY'
import sqlite3, sys
sqlite3.connect(sys.argv[1]).close()
PY
out=$(run "$bad_db"); rc=$?
check "query failure → exit 2"              '[ "$rc" -eq 2 ]'
check "query failure → wrapper ERROR"       'echo "$out" | grep -qF "ERROR: kanban-sweeper query failed (exit 2)"'
check "query failure → captured sqlite error" 'echo "$out" | grep -qF "ERROR: sqlite query failed: no such table: kanban_cards"'

# ----------------------------------------------------------------------------
echo "Task 16: the real ~/.claude-registry/kanban.db is reachable"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(bash "$SUT" 2>&1); rc=$?
  # Real board is expected to be clean (every Done analysis carries an
  # outcome witness) as of the gate's --since threshold. The earlier
  # `^OK: || WARNING:.*without outcome evidence` assertion was a partial
  # tautology: it passed whether the SUT said OK or warned about specific
  # offenders, so it never caught a regression that turned a clean board
  # into a stale one. Tighten to the exact clean-state line emitted by
  # scripts/check-analysis-outcomes.sh (SUT:check-analysis-outcomes.sh:202).
  # If the WARNING branch ever needs to be allowed again (e.g. a transient
  # historic-backlog sweep), document the carve-out here — don't relax the
  # assertion silently.
  check "real board → exit 0 (advisory)"           '[ "$rc" -eq 0 ]'
  check "real board → no python traceback"         '! echo "$out" | grep -qE "Traceback"'
  check "real board → clean-state OK line"         'echo "$out" | grep -qE "^OK: every Done analysis on this board carries outcome evidence"'
  check "real board → no WARNING emitted"          '! echo "$out" | grep -qE "WARNING:"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
