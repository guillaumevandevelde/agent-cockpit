#!/usr/bin/env bash
# Test harness for scripts/check-card-where-paths.sh.
#
# The SUT extracts path-looking tokens from the `Where:` line(s) of a kanban
# card's `## Evidence` block and `test -e`s each one against a repo root.
# Fixtures are a synthetic SQLite kanban DB plus a tmpdir "repo" holding a
# known set of real files — no dependency on the production board except the
# final real-board task.
#
# Tasks covered:
#   1.  arg parsing — `--help` works and mentions the real flags.
#   2.  error — missing DB → exit 2.
#   3.  error — missing repo → exit 2.
#   4.  error — unknown argument → exit 2.
#   5.  clean card (every Where: path exists) → exit 0 + OK, card not named.
#   6.  dead path → WARNING naming the card AND the missing path (advisory
#       exit 0).
#   7.  `file:line` suffix stripped — existing `foo.py:42` NOT flagged,
#       missing `gone.py:42` flagged as `gone.py` (acceptance criterion 2).
#   8.  `file:line-range` suffix stripped (`foo.py:12-30`).
#   9.  `module::symbol` suffix stripped (`foo.py::some_func`).
#  10.  `#anchor` suffix stripped (`doc.md#section`).
#  11.  glob patterns (`*.test.tsx`) are NOT treated as paths.
#  12.  adjacent backtick spans joined by `/` (`` `a`/`b` ``, the MCP
#       tool-name shape) do NOT fuse into a bogus path.
#  13.  two-segment prose slashes (`in/out`) are NOT treated as paths.
#  14.  URLs and absolute paths are NOT checked.
#  15.  directory refs with a trailing slash ARE checked.
#  16.  the bold `- **Where:**` variant is recognized.
#  17.  continuation lines of a wrapped Where: block are scanned.
#  18.  cards in the Done column are excluded.
#  19.  --strict: hits → exit 1; clean → exit 0.
#  20.  --card=ID scopes to one card; unknown id → exit 2.
#  21.  the same missing path twice in one card is reported once.
#  22.  markdown emphasis around a path (`**docs/x.md**`) is stripped, and
#       underscores in a filename survive (`_helpers.py`).
#  23.  real ~/.claude-registry/kanban.db is reachable AND the real board
#       emits the exact clean-state line. Deliberately NOT the tautological
#       `^OK:|WARNING:` shape — that passes in both the broken and the fixed
#       state (see self-improve card e5136a3f959d4886a7757b85e9d31f55).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-card-where-paths.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture repo — the set of paths that "exist". Anything a test references
# outside this list is expected to be flagged.
REPO="$TMP/repo"
mkdir -p "$REPO/backend/app/kanban" "$REPO/docs/cockpit" "$REPO/frontend/src/features/cc-bridge"
touch "$REPO/CLAUDE.md" \
      "$REPO/backend/app/kanban/dispatch.py" \
      "$REPO/backend/app/kanban/_helpers.py" \
      "$REPO/docs/cockpit/notes.md" \
      "$REPO/frontend/src/features/cc-bridge/types.ts"

# Minimal kanban DB with the columns the SUT selects.
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
        description TEXT,
        column TEXT,
        created_at DATETIME,
        updated_at DATETIME
    );
""")
con.commit(); con.close()
PY
}

# Insert a card. Args: db, id, title, column, description.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, title, col, desc = sys.argv[1:6]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, description, column,"
    " created_at, updated_at) VALUES (?, 'proj', ?, ?, ?, '2026-07-01', '2026-07-01')",
    (cid, title, desc, col),
)
con.commit(); con.close()
PY
}

# Run the SUT against the fixture DB + fixture repo. Extra args passthrough.
run() {
  local db="$1"; shift
  KANBAN_DB="$db" REPO_ROOT="$REPO" bash "$SUT" "$@" 2>&1
}

# Build a one-card DB and run it. Args: id, title, column, description, [flags...]
# Echoes combined output; exit code lands in $rc via the caller's `; rc=$?`.
one_card_run() {
  local id="$1" title="$2" col="$3" desc="$4"; shift 4
  local db="$TMP/db_$id.db"
  seed_db "$db"
  card "$db" "$id" "$title" "$col" "$desc"
  run "$db" "$@"
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help works and lists the real flags"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help runs without error"      'echo "$out" | grep -qF "check-card-where-paths.sh"'
check "--help mentions --strict"       'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --db"           'echo "$out" | grep -qE "\-\-db"'
check "--help mentions --repo"         'echo "$out" | grep -qE "\-\-repo"'
check "--help mentions --card"         'echo "$out" | grep -qE "\-\-card"'

# ----------------------------------------------------------------------------
echo "Task 2: error path — missing DB → exit 2"
out=$(KANBAN_DB="$TMP/nope.db" REPO_ROOT="$REPO" bash "$SUT" 2>&1); rc=$?
check "missing DB → exit 2"            '[ "$rc" -eq 2 ]'
check "missing DB → ERROR names it"    'echo "$out" | grep -qE "ERROR.*kanban DB"'

# ----------------------------------------------------------------------------
echo "Task 3: error path — missing repo → exit 2"
db3="$TMP/db3.db"; seed_db "$db3"
out=$(KANBAN_DB="$db3" REPO_ROOT="$TMP/no-such-repo" bash "$SUT" 2>&1); rc=$?
check "missing repo → exit 2"          '[ "$rc" -eq 2 ]'
check "missing repo → ERROR names it"  'echo "$out" | grep -qE "ERROR.*repo root"'

# ----------------------------------------------------------------------------
echo "Task 4: error path — unknown argument → exit 2"
out=$(run "$db3" --bogus); rc=$?
check "unknown arg → exit 2"           '[ "$rc" -eq 2 ]'
check "unknown arg → ERROR names flag" 'echo "$out" | grep -qF "unknown argument"'

# ----------------------------------------------------------------------------
echo "Task 5: clean card — every Where: path exists → OK"
out=$(one_card_run "CLEAN001" "[self-improve] all pointers live" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py` and `docs/cockpit/notes.md`
- Trigger: nothing
'); rc=$?
check "clean → exit 0"                 '[ "$rc" -eq 0 ]'
check "clean → prints OK"              'echo "$out" | grep -qE "^OK:"'
check "clean → no WARNING"             '! echo "$out" | grep -qE "WARNING:"'
check "clean → card not named"         '! echo "$out" | grep -qF "CLEAN001"'

# ----------------------------------------------------------------------------
echo "Task 6: dead path → WARNING naming card + missing path"
out=$(one_card_run "DEAD0001" "[self-improve] points at a ghost" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/ghost.py` — the handler
'); rc=$?
check "dead → exit 0 (advisory)"       '[ "$rc" -eq 0 ]'
check "dead → WARNING header"          'echo "$out" | grep -qE "WARNING:"'
check "dead → names the card"          'echo "$out" | grep -qF "DEAD0001"'
check "dead → names the missing path"  'echo "$out" | grep -qF "backend/app/kanban/ghost.py"'
check "dead → shows the column"        'echo "$out" | grep -qF "[Backlog]"'

# ----------------------------------------------------------------------------
echo "Task 7: file:line suffix stripped (acceptance criterion 2)"
out=$(one_card_run "LINE0001" "[self-improve] line refs" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py:42` and `backend/app/kanban/gone.py:42`
'); rc=$?
check "existing foo.py:42 → NOT flagged"  '! echo "$out" | grep -qF "dispatch.py"'
check "missing gone.py:42 → flagged"      'echo "$out" | grep -qF "backend/app/kanban/gone.py"'
check "missing reported without :42"      '! echo "$out" | grep -qF "gone.py:42   "'

# ----------------------------------------------------------------------------
echo "Task 8: file:line-range suffix stripped"
out=$(one_card_run "RANGE001" "[self-improve] range refs" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py:12-30`
'); rc=$?
check "existing foo.py:12-30 → exit 0"    '[ "$rc" -eq 0 ]'
check "existing foo.py:12-30 → OK"        'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 9: module::symbol suffix stripped"
out=$(one_card_run "SYM00001" "[self-improve] symbol refs" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py::_build_ship_instructions`
'); rc=$?
check "foo.py::sym → exit 0"              '[ "$rc" -eq 0 ]'
check "foo.py::sym → OK"                  'echo "$out" | grep -qE "^OK:"'
out=$(one_card_run "SYM00002" "[self-improve] dead symbol ref" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/ghost.py::_build_ship_instructions`
'); rc=$?
check "missing foo.py::sym → flagged as foo.py" 'echo "$out" | grep -qF "backend/app/kanban/ghost.py"'
# The raw token (suffix intact) is echoed as provenance; the *checked path*
# must have the symbol stripped, so assert on the `missing:` field only.
check "missing foo.py::sym → symbol not in checked path" '! echo "$out" | grep -qE "missing: [^ ]*::"'

# ----------------------------------------------------------------------------
echo "Task 10: #anchor suffix stripped"
out=$(one_card_run "ANCH0001" "[self-improve] anchor refs" "Backlog" \
'## Evidence
- Where: `docs/cockpit/notes.md#5-product-taal`
'); rc=$?
check "doc.md#anchor → exit 0"            '[ "$rc" -eq 0 ]'
check "doc.md#anchor → OK"                'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 11: glob patterns are NOT treated as paths"
out=$(one_card_run "GLOB0001" "[self-improve] glob mention" "Backlog" \
'## Evidence
- Where: `frontend/src/features/cc-bridge/` — no `*.test.tsx` files here
'); rc=$?
check "glob → exit 0"                     '[ "$rc" -eq 0 ]'
check "glob → OK (not flagged)"           'echo "$out" | grep -qE "^OK:"'
check "glob → .test.tsx not reported"     '! echo "$out" | grep -qF ".test.tsx"'

# ----------------------------------------------------------------------------
echo "Task 12: adjacent backtick spans joined by / do NOT fuse into a path"
out=$(one_card_run "MCP00001" "[self-improve] mcp tool names" "Backlog" \
'## Evidence
- Where: `mcp__cockpit-kanban__get_card`/`attach_deliverable` response shape
'); rc=$?
check "mcp a/b → exit 0"                  '[ "$rc" -eq 0 ]'
check "mcp a/b → OK (not flagged)"        'echo "$out" | grep -qE "^OK:"'
check "mcp a/b → no fused token reported" '! echo "$out" | grep -qF "get_card/attach_deliverable"'

# ----------------------------------------------------------------------------
echo "Task 13: two-segment prose slashes are NOT treated as paths"
out=$(one_card_run "PROSE001" "[self-improve] prose slashes" "Backlog" \
'## Evidence
- Where: the in/out boundary of the pr/branch and/or note deliverable
'); rc=$?
check "prose slash → exit 0"              '[ "$rc" -eq 0 ]'
check "prose slash → OK (not flagged)"    'echo "$out" | grep -qE "^OK:"'
check "prose slash → in/out not reported" '! echo "$out" | grep -qF "in/out"'

# ----------------------------------------------------------------------------
echo "Task 14: URLs and absolute paths are NOT checked"
out=$(one_card_run "URL00001" "[self-improve] urls and abs paths" "Backlog" \
'## Evidence
- Where: https://example.com/no/such/page.md and /var/lib/nowhere/thing.py
'); rc=$?
check "url/abs → exit 0"                  '[ "$rc" -eq 0 ]'
check "url/abs → OK (not flagged)"        'echo "$out" | grep -qE "^OK:"'
check "url/abs → url not reported"        '! echo "$out" | grep -qF "example.com"'
check "url/abs → abs path not reported"   '! echo "$out" | grep -qF "/var/lib/nowhere"'

# ----------------------------------------------------------------------------
echo "Task 15: directory refs with a trailing slash ARE checked"
out=$(one_card_run "DIR00001" "[self-improve] dir refs" "Backlog" \
'## Evidence
- Where: `frontend/src/features/cc-bridge/` exists, `frontend/src/features/ghost-dir/` does not
'); rc=$?
check "missing dir → flagged"             'echo "$out" | grep -qF "frontend/src/features/ghost-dir"'
check "existing dir → not flagged"        '! echo "$out" | grep -qF "cc-bridge"'

# ----------------------------------------------------------------------------
echo "Task 16: bold **Where:** variant is recognized"
out=$(one_card_run "BOLD0001" "[self-improve] bold where" "Backlog" \
'## Evidence
- **Where:** `backend/app/kanban/ghost.py` — the handler
'); rc=$?
check "bold Where → flagged"              'echo "$out" | grep -qF "backend/app/kanban/ghost.py"'
check "bold Where → names the card"       'echo "$out" | grep -qF "BOLD0001"'

# ----------------------------------------------------------------------------
echo "Task 17: continuation lines of a wrapped Where: block are scanned"
out=$(one_card_run "WRAP0001" "[self-improve] wrapped where" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py` (the docstring) +
  `backend/app/kanban/wrapped-ghost.py` on the next line
- Trigger: `backend/app/kanban/other-ghost.py` is a DIFFERENT bullet
'); rc=$?
check "continuation line scanned"         'echo "$out" | grep -qF "backend/app/kanban/wrapped-ghost.py"'
check "next bullet NOT scanned"           '! echo "$out" | grep -qF "other-ghost.py"'

# ----------------------------------------------------------------------------
echo "Task 18: cards in the Done column are excluded"
out=$(one_card_run "DONE0001" "[self-improve] done card with dead path" "Done" \
'## Evidence
- Where: `backend/app/kanban/ghost.py`
'); rc=$?
check "Done card → exit 0"                '[ "$rc" -eq 0 ]'
check "Done card → prints OK"             'echo "$out" | grep -qE "^OK:"'
check "Done card → not named"             '! echo "$out" | grep -qF "DONE0001"'

# ----------------------------------------------------------------------------
echo "Task 19: --strict round-trip"
out=$(one_card_run "STRICT01" "[self-improve] dead path" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/ghost.py`
' --strict); rc=$?
check "strict + hit → exit 1"             '[ "$rc" -eq 1 ]'
check "strict + hit → no advisory line"   '! echo "$out" | grep -qF "advisory — not failing"'
out=$(one_card_run "STRICT02" "[self-improve] live path" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/dispatch.py`
' --strict); rc=$?
check "strict + clean → exit 0"           '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 20: --card=ID scopes to one card; unknown id → exit 2"
multi_db="$TMP/multi.db"; seed_db "$multi_db"
card "$multi_db" "PICK0001" "[self-improve] first" "Backlog" \
'- Where: `backend/app/kanban/first-ghost.py`'
card "$multi_db" "PICK0002" "[self-improve] second" "Backlog" \
'- Where: `backend/app/kanban/second-ghost.py`'
out=$(run "$multi_db" --card=PICK0001); rc=$?
check "--card → reports the named card"   'echo "$out" | grep -qF "first-ghost.py"'
check "--card → ignores the other card"   '! echo "$out" | grep -qF "second-ghost.py"'
out=$(run "$multi_db"); rc=$?
check "no --card → reports both cards"    'echo "$out" | grep -qF "first-ghost.py" && echo "$out" | grep -qF "second-ghost.py"'
out=$(run "$multi_db" --card=NOSUCHID); rc=$?
check "--card unknown id → exit 2"        '[ "$rc" -eq 2 ]'
check "--card unknown id → ERROR"         'echo "$out" | grep -qE "ERROR.*no card with id"'
# A Done card is reachable by explicit --card (the column filter is a
# sweep-scope default, not a hard exclusion).
card "$multi_db" "PICK0003" "[self-improve] done" "Done" \
'- Where: `backend/app/kanban/done-ghost.py`'
out=$(run "$multi_db" --card=PICK0003); rc=$?
check "--card reaches a Done card"        'echo "$out" | grep -qF "done-ghost.py"'

# ----------------------------------------------------------------------------
echo "Task 21: same missing path twice in one card is reported once"
out=$(one_card_run "DEDUP001" "[self-improve] repeated path" "Backlog" \
'## Evidence
- Where: `backend/app/kanban/ghost.py:10` and again `backend/app/kanban/ghost.py:99`
'); rc=$?
n=$(echo "$out" | grep -cF "missing: backend/app/kanban/ghost.py")
check "repeated path reported once"       '[ "$n" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 22: emphasis stripped, underscores in filenames survive"
out=$(one_card_run "EMPH0001" "[self-improve] emphasis" "Backlog" \
'## Evidence
- Where: **docs/cockpit/notes.md** and `backend/app/kanban/_helpers.py`
'); rc=$?
check "emphasis+underscore → exit 0"      '[ "$rc" -eq 0 ]'
check "emphasis+underscore → OK"          'echo "$out" | grep -qE "^OK:"'
out=$(one_card_run "EMPH0002" "[self-improve] emphasis dead" "Backlog" \
'## Evidence
- Where: **docs/cockpit/ghost.md** and `backend/app/kanban/_ghost.py`
'); rc=$?
check "emphasis stripped before check"    'echo "$out" | grep -qF "missing: docs/cockpit/ghost.md"'
check "leading underscore preserved"      'echo "$out" | grep -qF "missing: backend/app/kanban/_ghost.py"'

# ----------------------------------------------------------------------------
echo "Task 23: the real ~/.claude-registry/kanban.db is reachable and clean"
if [ -r "$HOME/.claude-registry/kanban.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" \
        REPO_ROOT="$(dirname "$SCRIPT_DIR")" \
        bash "$SUT" 2>&1); rc=$?
  # Assert the EXACT clean-state line, not the `^OK:|WARNING:` tautology that
  # passes in both the broken and fixed state (self-improve card e5136a3f…).
  # If this starts failing, either the real board grew a genuinely dead
  # Where: pointer (fix the card) or the SUT's clean-state line drifted out
  # of sync with this grep.
  check "real board → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
  check "real board → no python traceback" '! echo "$out" | grep -qE "Traceback"'
  check "real board → clean-state OK line" 'echo "$out" | grep -qE "^OK: every path in a card Where: block exists"'
  check "real board → no WARNING emitted"  '! echo "$out" | grep -qE "WARNING:"'
else
  echo "  (skip — real kanban.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
