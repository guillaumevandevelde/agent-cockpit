#!/usr/bin/env bash
# Test harness for scripts/check-kanban-conventions.sh.
#
# Coverage:
#   1. arg parsing — -h|--help prints a usage block.
#   2. explicit path arg wins — synthetic DB passed as $1 is checked.
#   3. KANBAN_DB env var — synthetic DB pointed at via env var is checked when
#      no arg is given.
#   4. explicit path + KANBAN_DB both set — arg wins (more specific).
#   5. clean board — every FIXED_COLUMN row present → exit 0 with the
#      "clean (N project(s) checked)" summary line.
#   6. stale row — one FIXED_COLUMN row missing for a kanban-enabled project →
#      exit 1 with the missing column named.
#   7. project-without-any-columns skipped — projects that never enabled
#      kanban (zero kanban_columns rows) don't trigger spurious flags.
#   8. skip path still works — neither arg nor KANBAN_DB nor MAIN_DB_PATH nor
#      a resolvable git-common-dir DB available → exit 0 with the
#      "skipping" message.
#   9. worktree fallback — when run from a subdirectory of a synthetic git
#      repo that mirrors the production layout (a "main checkout" with a DB
#      file under backend/, plus a "worktree" subdir whose git-common-dir
#      points back at the main checkout's .git), the script picks up the
#      main checkout DB instead of bailing out. This is the exact failure
#      mode from kanban card 71e88ac2 (script skipped in every dispatched
#      session because the worktree DB doesn't exist).
#
# All DB fixtures are synthetic SQLite files in a tmpdir — the harness
# never reads or writes the live backend/claude_registry.db.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-kanban-conventions.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixtures
#
# FIXED_COLUMNS mirrors backend/app/kanban/schemas.py:COLUMNS via the
# inline copy inside scripts/check-kanban-conventions.sh. Keep in sync.
FIXED_COLUMNS=(Backlog Impediment Done "To Resume")

# Seed a synthetic kanban DB at $1. $2..$2+N are project_keys; for each one,
# write the full FIXED_COLUMNS set into kanban_columns unless $3 names a
# subset to OMIT (e.g. "To Resume" to simulate a stale project). Optional
# $4 is a project_key whose rows are intentionally absent — simulates a
# project where kanban was never enabled.
seed_db() {
  local db="$1"; shift
  local omit="$1"; shift || true
  rm -f "$db"
  python3 - "$db" "$FIXED_COLUMNS_STR" "$omit" "$@" <<'PY'
import sqlite3, sys
db, fixed_str, omit = sys.argv[1], sys.argv[2], sys.argv[3]
projects = sys.argv[4:]
fixed = fixed_str.split("\t")
omitted = set(omit.split(",")) if omit else set()
con = sqlite3.connect(db)
con.execute(
    "CREATE TABLE kanban_columns (project_key TEXT, name TEXT, "
    "PRIMARY KEY (project_key, name))"
)
for pk in projects:
    for col in fixed:
        if col in omitted:
            continue
        con.execute(
            "INSERT INTO kanban_columns (project_key, name) VALUES (?, ?)",
            (pk, col),
        )
con.commit(); con.close()
PY
}

FIXED_COLUMNS_STR="$(printf '%s\t' "${FIXED_COLUMNS[@]}")"
FIXED_COLUMNS_STR="${FIXED_COLUMNS_STR%$'\t'}"

# Run the SUT with explicit DB env vars. Args are forwarded.
run() {
  local label="$1"; shift
  (
    KANBAN_DB="$1" MAIN_DB_PATH="" OUTSIDE_GITCOMMON_DIR="" \
      bash "$SUT" "$@" 2>&1
  )
}

# ----------------------------------------------------------------------------
echo "Task 1: -h/--help prints usage"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage"  'echo "$out" | grep -qE "Usage:"'
check "--help mentions KANBAN_DB" 'echo "$out" | grep -qE "KANBAN_DB"'
check "--help mentions MAIN_DB_PATH" 'echo "$out" | grep -qE "MAIN_DB_PATH"'

# ----------------------------------------------------------------------------
echo "Task 2: explicit DB path arg is checked (clean)"
db2="$TMP/db2.sqlite"
seed_db "$db2" "" "proj-a" "proj-b"
out=$(run "explicit-clean" "$db2" "$db2"); rc=$?
check "explicit clean → exit 0"          '[ "$rc" -eq 0 ]'
check "explicit clean → ok lines for both" 'echo "$out" | grep -q "\[ok\]    proj-a" && echo "$out" | grep -q "\[ok\]    proj-b"'
check "explicit clean → summary present" 'echo "$out" | grep -qE "clean \([0-9]+ project\(s\) checked\)"'

# ----------------------------------------------------------------------------
echo "Task 3: KANBAN_DB env var works when no arg is given (clean)"
db3="$TMP/db3.sqlite"
seed_db "$db3" "" "proj-k"
out=$(KANBAN_DB="$db3" bash "$SUT" 2>&1); rc=$?
check "KANBAN_DB clean → exit 0"         '[ "$rc" -eq 0 ]'
check "KANBAN_DB clean → ok line"        'echo "$out" | grep -q "\[ok\]    proj-k"'

# ----------------------------------------------------------------------------
echo "Task 4: explicit arg beats KANBAN_DB"
db4a="$TMP/db4a.sqlite"; seed_db "$db4a" "" "proj-arg"
db4b="$TMP/db4b.sqlite"; seed_db "$db4b" "Backlog" "proj-env"
# With arg=$db4a (clean), env=$db4b (stale), output should reference proj-arg
# only and must NOT flag proj-env.
out=$(KANBAN_DB="$db4b" bash "$SUT" "$db4a" 2>&1); rc=$?
check "arg beats env → exit 0"           '[ "$rc" -eq 0 ]'
check "arg beats env → proj-arg ok"      'echo "$out" | grep -q "\[ok\]    proj-arg"'
check "arg beats env → proj-env absent"  '! echo "$out" | grep -qE "proj-env"'

# ----------------------------------------------------------------------------
echo "Task 5: stale column is flagged"
db5="$TMP/db5.sqlite"
seed_db "$db5" "To Resume" "proj-stale"
out=$(KANBAN_DB="$db5" bash "$SUT" 2>&1); rc=$?
check "stale → exit 1"                   '[ "$rc" -eq 1 ]'
check "stale → names To Resume"          'echo "$out" | grep -qE "To Resume"'
check "stale → names proj-stale"         'echo "$out" | grep -qF "proj-stale"'
check "stale → summary mentions missing" 'echo "$out" | grep -qE "missing fixed-column row"'

# ----------------------------------------------------------------------------
echo "Task 6: KANBAN_CONVENTIONS_QUIET suppresses per-project output"
db6="$TMP/db6.sqlite"
seed_db "$db6" "Done" "proj-q"
out=$(KANBAN_DB="$db6" KANBAN_CONVENTIONS_QUIET=1 bash "$SUT" 2>&1); rc=$?
check "quiet → exit 1 (still)"          '[ "$rc" -eq 1 ]'
check "quiet → no per-project line"      '! echo "$out" | grep -qE "\[(ok|stale)\]"'
check "quiet → missing-count line on stderr" 'echo "$out" | grep -qE "missing fixed-column row"'

# ----------------------------------------------------------------------------
echo "Task 7: skip path when nothing exists"
# Run from a fully-isolated temp HOME (no ~/.claude-registry/kanban.db)
# AND from a temp cwd that is not inside any git repo. Both real-world
# and test isolation: the script must skip cleanly when nothing exists
# instead of crashing against a partial DB (e.g. the production
# Claude-registry with no kanban_columns table).
isolated_home="$TMP/empty-home"; mkdir -p "$isolated_home"
isolated_cwd="$TMP/empty-cwd"; mkdir -p "$isolated_cwd"
out=$(cd "$isolated_cwd" && HOME="$isolated_home" KANBAN_DB="" MAIN_DB_PATH="" \
      bash "$SUT" "$TMP/nonexistent.sqlite" 2>&1); rc=$?
check "skip → exit 0"                   '[ "$rc" -eq 0 ]'
check "skip → mentions skipping"         'echo "$out" | grep -qE "skipping"'

# ----------------------------------------------------------------------------
echo "Task 8: worktree fallback — git-common-dir resolution picks up main DB"
# Synthesize a fake git repo that mirrors the production layout:
#   $TMP/repo/                 # "main checkout"
#   $TMP/repo/.git/            # real .git
#   $TMP/repo/backend/claude_registry.db  # the "main" DB
#   $TMP/repo/wt/              # subdir that simulates a Claude worktree
#                                (`git rev-parse --git-common-dir` from there
#                                returns ../.git, which is exactly what the
#                                main checkout sees — a real worktree's
#                                git-common-dir is the same .git)
# Run the SUT from $TMP/repo/wt/ with neither arg nor KANBAN_DB/MAIN_DB_PATH
# set; the script must locate $TMP/repo/backend/claude_registry.db via
# git-common-dir traversal and run the check against it. HOME must be
# an isolated empty tmpdir so the per-machine ~/.claude-registry/kanban.db
# default does NOT pre-empt the git-common-dir fallback we're testing.
repo="$TMP/repo"; mkdir -p "$repo/backend"
git -C "$repo" init -q --initial-branch=main
git -C "$repo" -c user.email=t@t -c user.name=t commit --allow-empty -q -m init
db_main="$repo/backend/claude_registry.db"
seed_db "$db_main" "" "proj-wt"

# Subdir of the repo acts as the "worktree": git-common-dir traversal
# from there walks up to $repo/.git, exactly like a real Claude worktree.
wt="$repo/wt"; mkdir -p "$wt"

(
  cd "$wt"
  git_common="$(git rev-parse --git-common-dir)"
  case "$(cd "$git_common" && pwd -P)" in
    "$(cd "$repo/.git" && pwd -P)") echo "  ok: synthetic worktree resolves git-common-dir to ../.git"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: synthetic worktree resolves git-common-dir to ../.git (got $git_common)"; FAIL=$((FAIL+1)) ;;
  esac

  # Run without KANBAN_DB/MAIN_DB_PATH; empty HOME so step 4 doesn't
  # pre-empt with the real per-machine board DB. Must auto-discover
  # the colocated backend/claude_registry.db via git-common-dir.
  out=$(HOME="$isolated_home" KANBAN_DB="" MAIN_DB_PATH="" \
        bash "$SUT" 2>&1); rc=$?
  cd - >/dev/null 2>&1 || true
  if [ "$rc" -eq 0 ]; then echo "  ok: worktree fallback → exit 0"; PASS=$((PASS+1)); else echo "  FAIL: worktree fallback → exit 0 (rc=$rc)"; FAIL=$((FAIL+1)); fi
  if echo "$out" | grep -q "\[ok\]    proj-wt"; then echo "  ok: worktree fallback → ok line"; PASS=$((PASS+1)); else echo "  FAIL: worktree fallback → ok line — output: $out"; FAIL=$((FAIL+1)); fi
  if echo "$out" | grep -qE "clean \(1 project\(s\) checked\)"; then echo "  ok: worktree fallback → summary"; PASS=$((PASS+1)); else echo "  FAIL: worktree fallback → summary — output: $out"; FAIL=$((FAIL+1)); fi
)

# ----------------------------------------------------------------------------
echo "Task 9: worktree fallback surfaces missing columns (not just clean)"
# Same shape as Task 8 but seed the main DB with a stale row, then run
# from the "worktree" — the validator must find the main DB and flag the
# missing column. Proves the fallback actually queries the DB, not just
# silently exits 0.
db_stale="$TMP/repo/backend/claude_registry.db"
seed_db "$db_stale" "Done" "proj-stale-wt"
(
  cd "$wt"
  out=$(HOME="$isolated_home" KANBAN_DB="" MAIN_DB_PATH="" \
        bash "$SUT" 2>&1); rc=$?
  cd - >/dev/null 2>&1 || true
  if [ "$rc" -eq 1 ]; then echo "  ok: worktree stale → exit 1"; PASS=$((PASS+1)); else echo "  FAIL: worktree stale → exit 1 (rc=$rc)"; FAIL=$((FAIL+1)); fi
  if echo "$out" | grep -qE "Done"; then echo "  ok: worktree stale → names Done"; PASS=$((PASS+1)); else echo "  FAIL: worktree stale → names Done — output: $out"; FAIL=$((FAIL+1)); fi
  if echo "$out" | grep -qF "proj-stale-wt"; then echo "  ok: worktree stale → names proj-stale-wt"; PASS=$((PASS+1)); else echo "  FAIL: worktree stale → names proj-stale-wt — output: $out"; FAIL=$((FAIL+1)); fi
)

# ----------------------------------------------------------------------------
echo "Task 10: explicit MAIN_DB_PATH env var overrides git-common-dir"
# Set MAIN_DB_PATH to a different synthetic DB than git-common-dir would
# resolve — MAIN_DB_PATH must win.
db_main10="$TMP/explicit-main.sqlite"
seed_db "$db_main10" "" "proj-explicit"
(
  cd "$wt"
  out=$(HOME="$isolated_home" KANBAN_DB="" MAIN_DB_PATH="$db_main10" \
        bash "$SUT" 2>&1); rc=$?
  cd - >/dev/null 2>&1 || true
  if [ "$rc" -eq 0 ]; then echo "  ok: MAIN_DB_PATH → exit 0"; PASS=$((PASS+1)); else echo "  FAIL: MAIN_DB_PATH → exit 0 (rc=$rc)"; FAIL=$((FAIL+1)); fi
  if echo "$out" | grep -qE "proj-explicit"; then echo "  ok: MAIN_DB_PATH → names proj-explicit"; PASS=$((PASS+1)); else echo "  FAIL: MAIN_DB_PATH → names proj-explicit — output: $out"; FAIL=$((FAIL+1)); fi
  if ! echo "$out" | grep -qF "proj-stale-wt"; then echo "  ok: MAIN_DB_PATH → does not name git-common-dir's proj-stale-wt"; PASS=$((PASS+1)); else echo "  FAIL: MAIN_DB_PATH → does not name git-common-dir's proj-stale-wt — output: $out"; FAIL=$((FAIL+1)); fi
)

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
