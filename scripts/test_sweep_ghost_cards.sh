#!/usr/bin/env bash
# Test harness for scripts/sweep_ghost_cards.py.
#
# Exercises the "ghost cards" sweeper against synthetic SQLite fixtures in
# a tempdir, so the tests stay green regardless of the board's real state.
# The real-board check is a final optional task — the production kanban.db may
# legitimately carry historic ghosts from pre-existing finished-but-not-closed
# cards, and we don't want flaky tests to depend on operator cleanup.
#
# The sweep flags every non-Done card that is "klaar maar niet gesloten":
# either (a) it carries a branch- or commit-deliverable whose tip demonstrably
# sits in origin/master (`git merge-base --is-ancestor`), or (b) the parent
# decomposed into ≥1 child card and every one of those children carries a
# `plan_ref` deliverable back to this parent (the analyst's role is over).
# A healthy card (no merged deliverable AND no decomposed children) is
# silently omitted; a Done card is skipped entirely.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  error — DB exists but has no kanban_cards table → exit 0 with a clean
#       report (table-mismatch shouldn't fail the sweep; nothing to sweep).
#   4.  clean board — zero non-terminal cards → totals all zero, exit 0.
#   5.  (a) non-terminal card with merged-branch deliverable → reported,
#       status="merged_deliverable".
#   6.  (a) non-terminal card with unmerged-branch deliverable → NOT reported.
#   7.  (a) non-terminal card with commit-deliverable in origin/master →
#       reported.
#   8.  (a) non-terminal card with commit-deliverable NOT in origin/master →
#       NOT reported.
#   9.  (b) parent with ≥1 child, every child carries plan_ref → reported,
#       status="decomposition_done".
#  10.  (b) parent with ≥1 child but one child missing plan_ref → NOT reported.
#  11.  (b) parent with ZERO children → NOT reported (no decomposition to
#       consider; the criterion is "alle aangemaakte kind-kaarten").
#  12.  Done card with a merged branch deliverable → NOT reported.
#  13.  mixed board — one of each interesting case → correct totals and
#       statuses.
#  14.  --strict with hits → exit 1; clean → exit 0.
#  15.  real ~/.claude-registry/kanban.db is reachable and reports JSON.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_ghost_cards.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: minimal kanban DB with the three tables the sweeper reads.
# Column order matches the live schema (PRAGMA table_info) so a future
# operator can paste the CREATE TABLE here from a real DB without surprises.
# Extra columns the sweep doesn't read are omitted on purpose — keep the
# fixture honest about what the script depends on. `column` is a SQLite
# keyword, so it is quoted here and in the SUT's queries.
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

# Card insert. Args: db id column parent_card_id title.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, column, parent, title = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
parent_val = None if parent == "" else parent
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, \"column\", parent_card_id) "
    "VALUES (?, ?, ?, ?, ?)",
    (cid, "proj", title, column, parent_val),
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
    "VALUES (?, ?, ?, ?, '2026-07-27 10:00:00')",
    (did, cid, kind, ref),
)
con.commit(); con.close()
PY
}

# ----------------------------------------------------------------------------
# Git fixture: a temp repo with a base commit, two branches off it, and one
# of them merged back. Used by Tasks 5–8 to verify `git merge-base
# --is-ancestor` is the right oracle. We deliberately use a fresh repo (not
# the worktree) so the test stays hermetic and cannot be polluted by stray
# branches or refs in the working tree.
seed_git() {
  local repo="$1"
  # `--initial-branch=master` (not `main`) so the sweeper's hardcoded
  # `origin/master` merge-base oracle resolves; using `main` would create
  # `origin/main` and the oracle would always fail on this fixture
  # (CLAUDE.md "GitHub default-branch ≠ main" — this repo, and many
  # popular ecosystems, still default to `master`).
  git init --quiet --initial-branch=master "$repo"
  git -C "$repo" config user.email "sweeper@test"
  git -C "$repo" config user.name "Sweeper Test"
  # Base commit so origin/master has a real SHA to compare against.
  printf 'base\n' > "$repo/file.txt"
  git -C "$repo" add file.txt
  git -C "$repo" commit --quiet -m "base"
  # Wire up an `origin` remote pointing at the same repo so the sweeper's
  # `git merge-base --is-ancestor X origin/master` can resolve `origin/master`.
  # Without this, the ref doesn't exist and the merge-base call silently fails.
  git -C "$repo" remote add origin "$repo"
  git -C "$repo" fetch origin
  # Branch MERGED — fast-forward back into master so it sits in master's history.
  git -C "$repo" checkout --quiet -b merged-branch
  printf 'merged\n' > "$repo/file.txt"
  git -C "$repo" commit --quiet --all -m "merged work"
  git -C "$repo" checkout --quiet master
  git -C "$repo" merge --quiet --ff-only merged-branch
  # Refresh origin/master after the merge so the merge-base oracle sees it.
  git -C "$repo" fetch origin
  # Branch UNMERGED — tip sits on a side branch only.
  git -C "$repo" checkout --quiet -b unmerged-branch
  printf 'unmerged\n' > "$repo/file.txt"
  git -C "$repo" commit --quiet --all -m "unmerged work"
  # A COMMIT-only deliverable: capture the SHA of a commit we know is in master.
  MERGED_COMMIT_SHA="$(git -C "$repo" rev-parse master)"
  # A COMMIT-only deliverable on the unmerged branch — never reached master.
  UNMERGED_COMMIT_SHA="$(git -C "$repo" rev-parse unmerged-branch)"
  printf 'export MERGED_COMMIT_SHA=%s\n' "$MERGED_COMMIT_SHA" > "$TMP/git.env"
  printf 'export UNMERGED_COMMIT_SHA=%s\n' "$UNMERGED_COMMIT_SHA" >> "$TMP/git.env"
}

# Run the SUT with KANBAN_DB + --repo-path pointed at the fixtures. Extra
# args are forwarded. Echoes stdout+stderr merged; captures exit code.
run() {
  local db="$1" repo="$2"; shift 2
  KANBAN_DB="$db" python3 "$SUT" --repo-path "$repo" "$@" 2>&1
}

# Same as run() but routes stderr to a file we can grep independently —
# the success-path tasks expect JSON on stdout and silence on stderr.
run_err() {
  local db="$1" repo="$2"; shift 2
  local errf="$TMP/err.$$.txt"
  local rc
  KANBAN_DB="$db" python3 "$SUT" --repo-path "$repo" "$@" 2>"$errf" 1>/dev/null
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
check "--help mentions --db"          'echo "$out" | grep -qE "\-\-db"'
check "--help mentions --strict"      'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --repo-path"   'echo "$out" | grep -qE "\-\-repo-path"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing DB → exit 2"
out=$(KANBAN_DB="$TMP/does-not-exist.db" python3 "$SUT" --repo-path "$REPO_ROOT" 2>&1); rc=$?
check "missing DB → exit 2"           '[ "$rc" -eq 2 ]'
check "missing DB → ERROR on stderr"  'echo "$out" | grep -qE "ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: DB exists but has no kanban_cards table → clean exit, empty report"
nokanban="$TMP/nokanban.db"; : > "$nokanban"  # zero-byte file, no tables
out=$(KANBAN_DB="$nokanban" python3 "$SUT" --repo-path "$REPO_ROOT" 2>&1); rc=$?
check "no kanban tables → exit 0"     '[ "$rc" -eq 0 ]'
check "no kanban tables → empty rows array" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0; assert d[\"rows\"]==[]"'

# ----------------------------------------------------------------------------
# Pre-build the git fixture once — Tasks 5–8 + 13 all need it.
GIT="$TMP/git-fixture"
seed_git "$GIT"
. "$TMP/git.env"

# ----------------------------------------------------------------------------
echo "Task 4: clean board — zero non-terminal cards → empty JSON report"
clean="$TMP/clean.db"; seed_db "$clean"
card "$clean" "DONE-1" "Done" "" "already done card"
out=$(run "$clean" "$GIT"); rc=$?
check "clean → exit 0"                '[ "$rc" -eq 0 ]'
check "clean → valid JSON"            'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → ghost_cards == 0"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "clean → rows == []"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: (a) non-terminal card with merged-branch deliverable → reported"
am="$TMP/am.db"; seed_db "$am"
card  "$am" "GHOST-B" "Backlog" "" "merged-branch card"
deliv "$am" "DLV-B-1" "GHOST-B" "branch" "merged-branch"
out=$(run "$am" "$GIT"); rc=$?
check "merged-branch → exit 0"        '[ "$rc" -eq 0 ]'
check "merged-branch → exactly 1 row" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, len(d[\"rows\"])"'
check "merged-branch → surfaces card id"  'echo "$out" | grep -qF "GHOST-B"'
check "merged-branch → status merged_deliverable" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"merged_deliverable\", r"'
check "merged-branch → evidence names branch"    'echo "$out" | grep -qF "merged-branch"'

# ----------------------------------------------------------------------------
echo "Task 6: (a) non-terminal card with UNMERGED-branch deliverable → not reported"
ub="$TMP/ub.db"; seed_db "$ub"
card  "$ub" "LIVE-B" "Backlog" "" "live-branch card"
deliv "$ub" "DLV-UB-1" "LIVE-B" "branch" "unmerged-branch"
out=$(run "$ub" "$GIT"); rc=$?
check "unmerged-branch → exit 0"      '[ "$rc" -eq 0 ]'
check "unmerged-branch → 0 ghost rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "unmerged-branch → card absent" '! echo "$out" | grep -qF "LIVE-B"'

# ----------------------------------------------------------------------------
echo "Task 7: (a) non-terminal card with commit-deliverable in main → reported"
ac="$TMP/ac.db"; seed_db "$ac"
card  "$ac" "GHOST-C" "Backlog" "" "merged-commit card"
deliv "$ac" "DLV-C-1" "GHOST-C" "commit" "$MERGED_COMMIT_SHA"
out=$(run "$ac" "$GIT"); rc=$?
check "merged-commit → exit 0"        '[ "$rc" -eq 0 ]'
check "merged-commit → exactly 1 row" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "merged-commit → status merged_deliverable" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"merged_deliverable\", r"'
check "merged-commit → evidence names sha" 'echo "$out" | grep -qF "$MERGED_COMMIT_SHA"'

# ----------------------------------------------------------------------------
echo "Task 8: (a) non-terminal card with commit-deliverable NOT in main → not reported"
uc="$TMP/uc.db"; seed_db "$uc"
card  "$uc" "LIVE-C" "Backlog" "" "unmerged-commit card"
deliv "$uc" "DLV-UC-1" "LIVE-C" "commit" "$UNMERGED_COMMIT_SHA"
out=$(run "$uc" "$GIT"); rc=$?
check "unmerged-commit → 0 ghost rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "unmerged-commit → card absent" '! echo "$out" | grep -qF "LIVE-C"'

# ----------------------------------------------------------------------------
echo "Task 9: (b) parent with ≥1 child, every child carries plan_ref → reported"
dp="$TMP/dp.db"; seed_db "$dp"
card  "$dp" "GHOST-D"  "Backlog" "" "decomposed parent"
card  "$dp" "CHILD-D1" "Backlog" "GHOST-D" "first child"
card  "$dp" "CHILD-D2" "Backlog" "GHOST-D" "second child"
deliv "$dp" "PLAN-D-1" "GHOST-D" "plan" "# plan body"
deliv "$dp" "PR-D-1"   "CHILD-D1" "plan_ref" '{"parent_card_id":"GHOST-D","plan_deliverable_id":"PLAN-D-1"}'
deliv "$dp" "PR-D-2"   "CHILD-D2" "plan_ref" '{"parent_card_id":"GHOST-D","plan_deliverable_id":"PLAN-D-1"}'
out=$(run "$dp" "$GIT"); rc=$?
check "decomposition-done → exit 0"  '[ "$rc" -eq 0 ]'
check "decomposition-done → exactly 1 row" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "decomposition-done → status matches" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d[\"rows\"][0]; assert r[\"status\"]==\"decomposition_done\", r"'
check "decomposition-done → surfaces card id" 'echo "$out" | grep -qF "GHOST-D"'

# ----------------------------------------------------------------------------
echo "Task 10: (b) parent with ≥1 child but one child missing plan_ref → not reported"
ip="$TMP/ip.db"; seed_db "$ip"
card  "$ip" "GHOST-IP"  "Backlog" "" "incomplete parent"
card  "$ip" "CHILD-IP1" "Backlog" "GHOST-IP" "child with ref"
card  "$ip" "CHILD-IP2" "Backlog" "GHOST-IP" "child WITHOUT ref"
deliv "$ip" "PLAN-IP-1" "GHOST-IP"  "plan" "# plan body"
deliv "$ip" "PR-IP-1"   "CHILD-IP1" "plan_ref" '{"parent_card_id":"GHOST-IP","plan_deliverable_id":"PLAN-IP-1"}'
# CHILD-IP2 has no plan_ref deliverable.
out=$(run "$ip" "$GIT"); rc=$?
check "incomplete-decomp → 0 ghost rows" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "incomplete-decomp → parent absent" '! echo "$out" | grep -qF "GHOST-IP"'

# ----------------------------------------------------------------------------
echo "Task 11: (b) parent with ZERO children → not reported (no decomposition to consider)"
zc="$TMP/zc.db"; seed_db "$zc"
card "$zc" "LONELY" "Backlog" "" "card without children"
out=$(run "$zc" "$GIT"); rc=$?
check "zero-children → 0 ghost rows"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "zero-children → card absent"   '! echo "$out" | grep -qF "LONELY"'

# ----------------------------------------------------------------------------
echo "Task 12: Done card with merged-branch deliverable → not reported"
done_db="$TMP/done.db"; seed_db "$done_db"
card  "$done_db" "DONE-B" "Done" "" "already done with merged branch"
deliv "$done_db" "DLV-DB-1" "DONE-B" "branch" "merged-branch"
out=$(run "$done_db" "$GIT"); rc=$?
check "Done + merged → 0 ghost rows"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==0, d[\"totals\"]"'
check "Done + merged → card absent"   '! echo "$out" | grep -qF "DONE-B"'

# ----------------------------------------------------------------------------
echo "Task 13: mixed board — ghost-merged + ghost-decomposed + healthy + Done → correct totals"
mix="$TMP/mix.db"; seed_db "$mix"
# Ghost-merged (a) — Backlog card with merged branch.
card  "$mix" "MX-M" "Backlog" "" "ghost via merged"
deliv "$mix" "DLV-MX-M" "MX-M" "branch" "merged-branch"
# Ghost-decomposed (b) — Backlog parent whose both children carry plan_refs.
card  "$mix" "MX-D" "Backlog" "" "ghost via decomposition"
card  "$mix" "MX-D1" "Backlog" "MX-D" "child 1"
card  "$mix" "MX-D2" "Backlog" "MX-D" "child 2"
deliv "$mix" "PLAN-MX-D" "MX-D"  "plan" "# plan"
deliv "$mix" "PR-MX-D1" "MX-D1" "plan_ref" '{"parent_card_id":"MX-D","plan_deliverable_id":"PLAN-MX-D"}'
deliv "$mix" "PR-MX-D2" "MX-D2" "plan_ref" '{"parent_card_id":"MX-D","plan_deliverable_id":"PLAN-MX-D"}'
# Healthy — Backlog card with unmerged branch (not a ghost).
card  "$mix" "MX-H" "Backlog" "" "healthy"
deliv "$mix" "DLV-MX-H" "MX-H" "branch" "unmerged-branch"
# Done with merged branch — skipped entirely.
card  "$mix" "MX-DONE" "Done" "" "done"
deliv "$mix" "DLV-MX-DONE" "MX-DONE" "branch" "merged-branch"
out=$(run "$mix" "$GIT"); rc=$?
check "mixed → exit 0 (advisory)"    '[ "$rc" -eq 0 ]'
check "mixed → ghost_cards == 2"     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"ghost_cards\"]==2, d[\"totals\"]"'
check "mixed → by_status breaks out per category" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"by_status\"][\"merged_deliverable\"]==1 and d[\"totals\"][\"by_status\"][\"decomposition_done\"]==1, d[\"totals\"]"'
check "mixed → ghost ids are MX-M + MX-D" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); ids=sorted(r[\"card_id\"] for r in d[\"rows\"]); assert ids==[\"MX-D\",\"MX-M\"], ids"'
check "mixed → healthy MX-H absent"  '! echo "$out" | grep -qF "MX-H"'
check "mixed → done MX-DONE absent"  '! echo "$out" | grep -qF "MX-DONE"'

# ----------------------------------------------------------------------------
echo "Task 14: --strict with hits → exit 1; clean → exit 0"
out=$(run "$am" "$GIT" --strict); rc=$?
check "strict + hits → exit 1"       '[ "$rc" -eq 1 ]'
out=$(run "$clean" "$GIT" --strict); rc=$?
check "strict + clean → exit 0"      '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 15: real ~/.claude-registry/kanban.db is reachable and reports JSON"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" python3 "$SUT" --repo-path "$REPO_ROOT" 2>&1); rc=$?
  check "real board → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
  check "real board → valid JSON"      'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/kanban.db not present)"
fi

# ----------------------------------------------------------------------------
# Specific clean-state assertion (kaart e5136a3f — niet `grep -qE "^OK:|WARNING:"`).
# De e5136a3f-conventie verbiedt tautologische grep die zowel broken als fixed
# doorlaat. Hier tellen we exact het aantal FAIL-regels en vergelijken met 0;
# een hernoeming van `bad()` of een print van WARNING zou de assertion niet
# per ongeluk groen maken.
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
