#!/usr/bin/env bash
# Test harness for scripts/sweep_merged_but_open_cards.py.
#
# Exercises the merged-but-open sweeper against synthetic SQLite + git fixtures
# in a tempdir, so the tests stay green regardless of the live board's and
# origin's state. The real-board check is a final optional task — the production
# kanban.db legitimately carries historic merged-but-open cards, and we don't
# want flaky tests to depend on operator cleanup.
#
# A card is a hit when it is NOT in `Done` and at least one of its `commit` /
# `branch` deliverables is already contained in the base ref (default
# origin/master): `merge-base --is-ancestor` for a commit sha, zero `+` lines
# from `git cherry` for a branch. Those cards are the ones auto-dispatch keeps
# re-picking, burning a duplicate session on work already on master.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  error — missing/non-git repo → exit 2 + ERROR on stderr.
#   4.  error — unresolvable --base-ref → exit 2 (never a silent all-clear).
#   5.  DB without the kanban tables → exit 0 with a clean report.
#   6.  clean board (no commit/branch deliverables) → totals zero, exit 0.
#   7.  merged branch on a Backlog card → 1 row, dispatchable, surfaces ref.
#   8.  unmerged (live) branch on a Backlog card → not reported.
#   9.  Done card with a merged branch → not reported (Done is skipped).
#  10.  merged commit sha deliverable → reported (ancestry path).
#  11.  deleted/unknown ref → unresolved_refs, NOT a hit.
#  12.  non-dispatch column (Awaiting Subtasks) → reported but dispatchable=false;
#       --dispatchable-only drops it while keeping the Backlog hit.
#  13.  agent-lane column (engineer) counts as dispatchable.
#  14.  non-git deliverable kinds (pr/link/note) are ignored.
#  15.  --project-key filters to one project.
#  16.  --strict with hits → exit 1; --strict clean → exit 0.
#  17.  mixed board — totals add up over 5 cards.
#  18.  real board + real repo → valid JSON, no traceback (optional).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_merged_but_open_cards.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with the two tables the sweeper joins. Column
# order matches the live schema (a subset) so a future operator can paste the
# CREATE TABLE from a real DB without surprises. `column` is a SQLite keyword,
# so it is quoted here and in the SUT's query.
seed_db() {
  local db="$1"
  rm -f "$db"
  python3 - "$db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript("""
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        project_key TEXT,
        title TEXT,
        "column" TEXT NOT NULL DEFAULT 'Backlog'
    );
    CREATE TABLE kanban_deliverables (
        id TEXT PRIMARY KEY,
        card_id TEXT,
        kind TEXT,
        ref TEXT
    );
""")
con.commit(); con.close()
PY
}

# Card insert. Args: db id column project_key title
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, column, pkey, title = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    'INSERT INTO kanban_cards (id, project_key, title, "column") VALUES (?,?,?,?)',
    (cid, pkey, title, column),
)
con.commit(); con.close()
PY
}

# Deliverable insert. Args: db card_id kind ref
deliv() {
  python3 - "$@" <<'PY'
import sqlite3, sys, uuid
db, cid, kind, ref = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_deliverables (id, card_id, kind, ref) VALUES (?,?,?,?)",
    (uuid.uuid4().hex, cid, kind, ref),
)
con.commit(); con.close()
PY
}

# A minimal local-only git repo on `master` with refs/remotes/origin/master
# mirrored via update-ref (no network; --no-fetch is the harness default).
make_repo() {
  local root="$1"
  rm -rf "$root"; mkdir -p "$root"
  (
    cd "$root"
    git init -q -b master
    git config user.email "t@t"; git config user.name "t"
    : > m1.txt && git add m1.txt && git commit -qm m1
    : > m2.txt && git add m2.txt && git commit -qm m2
    git update-ref refs/remotes/origin/master "$(git rev-parse master)"
  )
}

# Branch off master with one commit; echoes its sha. Args: root branch file
add_branch() {
  local root="$1" br="$2" file="$3"
  (
    cd "$root"
    git checkout -q master
    git checkout -q -b "$br"
    echo "$br" > "$file" && git add "$file" && git commit -qm "$br"
    git rev-parse HEAD
  )
}

# Merge a branch into master and refresh refs/remotes/origin/master, i.e. the
# "shipped" state. Args: root branch
land_on_master() {
  local root="$1" br="$2"
  (
    cd "$root"
    git checkout -q master
    git merge -q --no-ff "$br" -m "Merge $br" >/dev/null 2>&1
    git update-ref refs/remotes/origin/master "$(git rev-parse master)"
    git checkout -q master
  )
}

# Run the SUT against the fixtures with --no-fetch. Extra args forwarded.
run() {
  local db="$1" repo="$2"; shift 2
  KANBAN_DB="$db" python3 "$SUT" --repo "$repo" --no-fetch "$@" 2>&1
}

# jq-free JSON assert helper: pipes stdin into a python expression on `d`.
jassert() { python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert $1, d"; }

# ----------------------------------------------------------------------------
echo "Task 1: --help runs and lists all real flags + synopsis"
out=$(python3 "$SUT" --help 2>&1); rc=$?
check "--help runs without error"          '[ "$rc" -eq 0 ]'
check "--help shows synopsis"              'echo "$out" | grep -qE "^usage:"'
check "--help mentions --db"               'echo "$out" | grep -qE "\-\-db"'
check "--help mentions --repo"             'echo "$out" | grep -qE "\-\-repo"'
check "--help mentions --remote"           'echo "$out" | grep -qE "\-\-remote"'
check "--help mentions --base-ref"         'echo "$out" | grep -qE "\-\-base-ref"'
check "--help mentions --project-key"      'echo "$out" | grep -qE "\-\-project-key"'
check "--help mentions --dispatchable-only" 'echo "$out" | grep -qE "\-\-dispatchable-only"'
check "--help mentions --no-fetch"         'echo "$out" | grep -qE "\-\-no-fetch"'
check "--help mentions --strict"           'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --json"             'echo "$out" | grep -qE "\-\-json"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing DB → exit 2"
base="$TMP/base"; make_repo "$base"
out=$(run "$TMP/nope.db" "$base"); rc=$?
check "missing DB → exit 2"                '[ "$rc" -eq 2 ]'
check "missing DB → ERROR on stderr"       'echo "$out" | grep -qE "ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: error — missing / non-git repo → exit 2"
db0="$TMP/db0.db"; seed_db "$db0"
out=$(run "$db0" "$TMP/no-such-repo"); rc=$?
check "missing repo → exit 2"              '[ "$rc" -eq 2 ]'
check "missing repo → ERROR on stderr"     'echo "$out" | grep -qE "ERROR"'
notgit="$TMP/notgit"; mkdir -p "$notgit"
out=$(run "$db0" "$notgit"); rc=$?
check "non-git dir → exit 2"               '[ "$rc" -eq 2 ]'
check "non-git dir → mentions git"         'echo "$out" | grep -qiE "git"'

# ----------------------------------------------------------------------------
echo "Task 4: error — unresolvable --base-ref → exit 2 (no silent all-clear)"
out=$(run "$db0" "$base" --base-ref origin/does-not-exist); rc=$?
check "bad base-ref → exit 2"              '[ "$rc" -eq 2 ]'
check "bad base-ref → names the ref"       'echo "$out" | grep -qF "origin/does-not-exist"'
check "bad base-ref → no JSON all-clear"   '! echo "$out" | grep -qF "\"cards_merged_but_open\""'

# ----------------------------------------------------------------------------
echo "Task 5: DB without the kanban tables → clean report, exit 0"
notables="$TMP/notables.db"; : > "$notables"
out=$(run "$notables" "$base"); rc=$?
check "no tables → exit 0"                 '[ "$rc" -eq 0 ]'
check "no tables → empty rows"             'echo "$out" | jassert "d[\"rows\"]==[]"'
check "no tables → zero hits"              'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==0"'

# ----------------------------------------------------------------------------
echo "Task 6: clean board (no commit/branch deliverables) → zero totals"
cleandb="$TMP/clean.db"; seed_db "$cleandb"
card "$cleandb" "A" "Backlog" "proj" "no deliverables"
out=$(run "$cleandb" "$base"); rc=$?
check "clean → exit 0"                     '[ "$rc" -eq 0 ]'
check "clean → valid JSON"                 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → cards_scanned == 0"         'echo "$out" | jassert "d[\"totals\"][\"cards_scanned\"]==0"'
check "clean → rows == []"                 'echo "$out" | jassert "d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 7: merged branch on a Backlog card → 1 dispatchable row"
r7="$TMP/r7"; make_repo "$r7"
add_branch "$r7" "k-shipped-1111" "s1.txt" >/dev/null
land_on_master "$r7" "k-shipped-1111"
db7="$TMP/db7.db"; seed_db "$db7"
card  "$db7" "CARD-MERGED" "Backlog" "proj" "shipped but open"
deliv "$db7" "CARD-MERGED" "branch" "k-shipped-1111"
out=$(run "$db7" "$r7"); rc=$?
check "merged branch → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
check "merged branch → exactly 1 row"      'echo "$out" | jassert "len(d[\"rows\"])==1"'
check "merged branch → surfaces card id"   'echo "$out" | grep -qF "CARD-MERGED"'
check "merged branch → surfaces title"     'echo "$out" | grep -qF "shipped but open"'
check "merged branch → surfaces ref"       'echo "$out" | grep -qF "k-shipped-1111"'
check "merged branch → dispatchable true"  'echo "$out" | jassert "d[\"rows\"][0][\"dispatchable\"] is True"'
check "merged branch → resolved via refs/heads" 'echo "$out" | jassert "d[\"rows\"][0][\"merged_refs\"][0][\"resolved_ref\"]==\"refs/heads/k-shipped-1111\""'
check "merged branch → hits == 1"          'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==1"'
check "merged branch → dispatchable_hits == 1" 'echo "$out" | jassert "d[\"totals\"][\"dispatchable_hits\"]==1"'
check "merged branch → merged_refs == 1"   'echo "$out" | jassert "d[\"totals\"][\"merged_refs\"]==1"'

# ----------------------------------------------------------------------------
echo "Task 8: unmerged (live) branch → not reported"
r8="$TMP/r8"; make_repo "$r8"
add_branch "$r8" "k-live-2222" "l1.txt" >/dev/null
db8="$TMP/db8.db"; seed_db "$db8"
card  "$db8" "CARD-LIVE" "Backlog" "proj" "still in flight"
deliv "$db8" "CARD-LIVE" "branch" "k-live-2222"
out=$(run "$db8" "$r8"); rc=$?
check "live branch → exit 0"               '[ "$rc" -eq 0 ]'
check "live branch → 0 rows"               'echo "$out" | jassert "d[\"rows\"]==[]"'
check "live branch → counted as scanned"   'echo "$out" | jassert "d[\"totals\"][\"cards_scanned\"]==1"'
check "live branch → card absent"          '! echo "$out" | grep -qF "CARD-LIVE"'

# ----------------------------------------------------------------------------
echo "Task 9: Done card with a merged branch → skipped"
db9="$TMP/db9.db"; seed_db "$db9"
card  "$db9" "CARD-DONE" "Done" "proj" "properly closed"
deliv "$db9" "CARD-DONE" "branch" "k-shipped-1111"
out=$(run "$db9" "$r7"); rc=$?
check "Done card → exit 0"                 '[ "$rc" -eq 0 ]'
check "Done card → 0 hits"                 'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==0"'
check "Done card → not reported"           '! echo "$out" | grep -qF "CARD-DONE"'

# ----------------------------------------------------------------------------
echo "Task 10: merged commit sha deliverable → reported (ancestry path)"
r10="$TMP/r10"; make_repo "$r10"
sha10=$(add_branch "$r10" "k-commit-3333" "c1.txt")
land_on_master "$r10" "k-commit-3333"
db10="$TMP/db10.db"; seed_db "$db10"
card  "$db10" "CARD-SHA" "engineer" "proj" "commit deliverable"
deliv "$db10" "CARD-SHA" "commit" "${sha10:0:8}"
out=$(run "$db10" "$r10"); rc=$?
check "merged sha → exit 0"                '[ "$rc" -eq 0 ]'
check "merged sha → 1 row"                 'echo "$out" | jassert "len(d[\"rows\"])==1"'
check "merged sha → kind commit"           'echo "$out" | jassert "d[\"rows\"][0][\"merged_refs\"][0][\"kind\"]==\"commit\""'
check "merged sha → surfaces short sha"    'echo "$out" | grep -qF "${sha10:0:8}"'
# An unmerged sha must NOT be reported: a fresh commit on a live branch.
r10b="$TMP/r10b"; make_repo "$r10b"
sha10b=$(add_branch "$r10b" "k-live-4444" "c2.txt")
db10b="$TMP/db10b.db"; seed_db "$db10b"
card  "$db10b" "CARD-SHA-LIVE" "Backlog" "proj" "unmerged commit"
deliv "$db10b" "CARD-SHA-LIVE" "commit" "$sha10b"
out=$(run "$db10b" "$r10b"); rc=$?
check "unmerged sha → 0 hits"              'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==0"'

# ----------------------------------------------------------------------------
echo "Task 11: unknown/deleted ref → unresolved_refs, not a hit"
db11="$TMP/db11.db"; seed_db "$db11"
card  "$db11" "CARD-GONE" "Backlog" "proj" "ref deleted everywhere"
deliv "$db11" "CARD-GONE" "branch" "k-vanished-9999"
out=$(run "$db11" "$base"); rc=$?
check "unknown ref → exit 0"               '[ "$rc" -eq 0 ]'
check "unknown ref → 0 hits"               'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==0"'
check "unknown ref → unresolved_refs == 1" 'echo "$out" | jassert "d[\"totals\"][\"unresolved_refs\"]==1"'
check "unknown ref → not in rows"          'echo "$out" | jassert "d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 12: non-dispatch column reported as dispatchable=false; --dispatchable-only drops it"
db12="$TMP/db12.db"; seed_db "$db12"
card  "$db12" "CARD-PARKED" "Awaiting Subtasks" "proj" "parked parent"
deliv "$db12" "CARD-PARKED" "branch" "k-shipped-1111"
card  "$db12" "CARD-BACKLOG" "Backlog" "proj" "dispatchable hit"
deliv "$db12" "CARD-BACKLOG" "branch" "k-shipped-1111"
out=$(run "$db12" "$r7"); rc=$?
check "parked → exit 0"                    '[ "$rc" -eq 0 ]'
check "parked → 2 rows by default"         'echo "$out" | jassert "len(d[\"rows\"])==2"'
check "parked → dispatchable_hits == 1"    'echo "$out" | jassert "d[\"totals\"][\"dispatchable_hits\"]==1"'
check "parked → parked row dispatchable=false" 'echo "$out" | jassert "[r for r in d[\"rows\"] if r[\"card_id\"]==\"CARD-PARKED\"][0][\"dispatchable\"] is False"'
check "parked → dispatchable rows sort first" 'echo "$out" | jassert "d[\"rows\"][0][\"card_id\"]==\"CARD-BACKLOG\""'
out=$(run "$db12" "$r7" --dispatchable-only); rc=$?
check "--dispatchable-only → 1 row"        'echo "$out" | jassert "len(d[\"rows\"])==1"'
check "--dispatchable-only → keeps Backlog" 'echo "$out" | jassert "d[\"rows\"][0][\"card_id\"]==\"CARD-BACKLOG\""'
check "--dispatchable-only → drops parked" '! echo "$out" | grep -qF "CARD-PARKED"'
check "--dispatchable-only → echoed in report" 'echo "$out" | jassert "d[\"dispatchable_only\"] is True"'

# ----------------------------------------------------------------------------
echo "Task 13: agent-lane column (engineer/reviewer) counts as dispatchable"
db13="$TMP/db13.db"; seed_db "$db13"
card  "$db13" "CARD-LANE" "engineer" "proj" "stuck in the engineer lane"
deliv "$db13" "CARD-LANE" "branch" "k-shipped-1111"
out=$(run "$db13" "$r7"); rc=$?
check "agent lane → 1 row"                 'echo "$out" | jassert "len(d[\"rows\"])==1"'
check "agent lane → dispatchable true"     'echo "$out" | jassert "d[\"rows\"][0][\"dispatchable\"] is True"'
check "agent lane → survives --dispatchable-only" 'run "$db13" "$r7" --dispatchable-only | grep -qF "CARD-LANE"'

# ----------------------------------------------------------------------------
echo "Task 14: non-git deliverable kinds are ignored"
db14="$TMP/db14.db"; seed_db "$db14"
card  "$db14" "CARD-PR" "Backlog" "proj" "pr and link only"
deliv "$db14" "CARD-PR" "pr"   "k-shipped-1111"
deliv "$db14" "CARD-PR" "link" "https://example.test/x"
deliv "$db14" "CARD-PR" "note" "k-shipped-1111"
out=$(run "$db14" "$r7"); rc=$?
check "non-git kinds → cards_scanned == 0" 'echo "$out" | jassert "d[\"totals\"][\"cards_scanned\"]==0"'
check "non-git kinds → 0 rows"             'echo "$out" | jassert "d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
echo "Task 15: --project-key filters to one project"
db15="$TMP/db15.db"; seed_db "$db15"
card  "$db15" "CARD-P1" "Backlog" "proj-one" "in project one"
deliv "$db15" "CARD-P1" "branch" "k-shipped-1111"
card  "$db15" "CARD-P2" "Backlog" "proj-two" "in project two"
deliv "$db15" "CARD-P2" "branch" "k-shipped-1111"
out=$(run "$db15" "$r7" --project-key proj-one); rc=$?
check "project filter → 1 row"             'echo "$out" | jassert "len(d[\"rows\"])==1"'
check "project filter → keeps proj-one"    'echo "$out" | jassert "d[\"rows\"][0][\"card_id\"]==\"CARD-P1\""'
check "project filter → drops proj-two"    '! echo "$out" | grep -qF "CARD-P2"'
check "project filter → echoed in report"  'echo "$out" | jassert "d[\"project_key\"]==\"proj-one\""'

# ----------------------------------------------------------------------------
echo "Task 16: --strict with hits → exit 1; clean → exit 0"
out=$(run "$db7" "$r7" --strict); rc=$?
check "strict + hits → exit 1"             '[ "$rc" -eq 1 ]'
out=$(run "$cleandb" "$base" --strict); rc=$?
check "strict + clean → exit 0"            '[ "$rc" -eq 0 ]'
out=$(run "$db12" "$r7" --strict --dispatchable-only); rc=$?
check "strict + dispatchable hit → exit 1" '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 17: mixed board — totals add up"
r17="$TMP/r17"; make_repo "$r17"
add_branch "$r17" "k-m-a" "a.txt" >/dev/null; land_on_master "$r17" "k-m-a"
add_branch "$r17" "k-m-b" "b.txt" >/dev/null; land_on_master "$r17" "k-m-b"
add_branch "$r17" "k-live-c" "c.txt" >/dev/null
db17="$TMP/db17.db"; seed_db "$db17"
card  "$db17" "M-1" "Backlog"           "proj" "merged, dispatchable"
deliv "$db17" "M-1" "branch" "k-m-a"
card  "$db17" "M-2" "reviewer"          "proj" "merged in a lane"
deliv "$db17" "M-2" "branch" "k-m-b"
card  "$db17" "M-3" "Impediment"        "proj" "merged but blocked"
deliv "$db17" "M-3" "branch" "k-m-a"
card  "$db17" "L-1" "Backlog"           "proj" "live work"
deliv "$db17" "L-1" "branch" "k-live-c"
card  "$db17" "D-1" "Done"              "proj" "closed properly"
deliv "$db17" "D-1" "branch" "k-m-b"
out=$(run "$db17" "$r17"); rc=$?
check "mixed → exit 0 (advisory)"          '[ "$rc" -eq 0 ]'
check "mixed → cards_scanned == 4"         'echo "$out" | jassert "d[\"totals\"][\"cards_scanned\"]==4"'
check "mixed → hits == 3"                  'echo "$out" | jassert "d[\"totals\"][\"cards_merged_but_open\"]==3"'
check "mixed → dispatchable_hits == 2"     'echo "$out" | jassert "d[\"totals\"][\"dispatchable_hits\"]==2"'
check "mixed → exact hit ids"              'echo "$out" | jassert "{r[\"card_id\"] for r in d[\"rows\"]}=={\"M-1\",\"M-2\",\"M-3\"}"'
check "mixed → live card absent"           '! echo "$out" | grep -qF "L-1"'
check "mixed → Done card absent"           '! echo "$out" | grep -qF "D-1"'
out=$(run "$db17" "$r17" --dispatchable-only); rc=$?
check "mixed + dispatchable-only → 2 rows" 'echo "$out" | jassert "{r[\"card_id\"] for r in d[\"rows\"]}=={\"M-1\",\"M-2\"}"'

# ----------------------------------------------------------------------------
echo "Task 18: real board + real repo → valid JSON, no traceback"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" python3 "$SUT" \
        --repo "$REPO_ROOT" --no-fetch 2>&1); rc=$?
  check "real board → exit 0 (advisory)"   '[ "$rc" -eq 0 ]'
  check "real board → valid JSON"          'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
