#!/usr/bin/env bash
# Test harness for scripts/sweep_invalid_column_models.py.
#
# Exercises the invalid-(provider, model) column sweeper against synthetic
# SQLite fixtures in a tempdir, so the tests stay green regardless of the
# board's real state.
#
# The sweep mirrors `_allowed_models_for_provider` in
# backend/app/api/v1/kanban/router.py: a column whose (default_provider,
# default_model) pair the API would reject with 422 is a hit. Rows written
# before that guard landed were never migrated, and a stale row is worse than
# a rejected one — the dispatcher falls through to an unrelated model, and the
# settings dialog can no longer save the column at all.
#
# Tasks covered:
#   1.  --help runs and lists the real flags.
#   2.  error — missing DB → exit 2 + ERROR on stderr.
#   3.  DB without a kanban_columns table → exit 0, clean report.
#   4.  clean board — every pair valid → totals.invalid == 0, exit 0.
#   5.  (minimax, opus) → exactly one hit, reason names both sides.
#   6.  (anthropic, MiniMax-M3) → one hit (symmetric guard).
#   7.  (bedrock, <ARN>) → NOT a hit; bedrock has no closed set.
#   8.  null model / null provider → NOT a hit (defer to dispatch chain).
#   9.  cache respected — a model present in kanban_meta is valid even though
#       it is absent from the hardcoded seed.
#  10.  missing kanban_meta row → seed used as fallback, not "everything invalid".
#  11.  --strict with hits → exit 1; --strict on a clean board → exit 0.
#  12.  --fix clears default_model to NULL and is idempotent; without --fix the
#       sweeper does not write.
#  13.  --strict --fix → exit 0 (the hits were resolved, not ignored).
#  14.  real ~/.claude-registry/kanban.db is reachable.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/sweep_invalid_column_models.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Fixture: only the two tables the sweeper reads. Column order matches the
# live schema so a future operator can paste CREATE TABLE from a real DB.
seed_db() {
  local db="$1"
  rm -f "$db"
  python3 - "$db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript("""
    CREATE TABLE kanban_columns (
        id TEXT PRIMARY KEY,
        project_key TEXT NOT NULL,
        name TEXT NOT NULL,
        default_provider TEXT,
        default_model TEXT
    );
    CREATE TABLE kanban_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
""")
con.commit(); con.close()
PY
}

# Column insert. Args: db id name provider model ("" → NULL).
col() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, name, provider, model = sys.argv[1:6]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_columns (id, project_key, name, default_provider, "
    "default_model) VALUES (?, 'PROJ', ?, ?, ?)",
    (cid, name, provider or None, model or None),
)
con.commit(); con.close()
PY
}

# Meta insert. Args: db key json_value.
meta() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, key, value = sys.argv[1:4]
con = sqlite3.connect(db)
con.execute("INSERT INTO kanban_meta (key, value) VALUES (?, ?)", (key, value))
con.commit(); con.close()
PY
}

# Read one field out of a column row. Args: db id field.
col_field() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, field = sys.argv[1:4]
con = sqlite3.connect(db)
row = con.execute(f"SELECT {field} FROM kanban_columns WHERE id=?", (cid,)).fetchone()
print("NULL" if row[0] is None else row[0])
con.close()
PY
}

jqf() { python3 -c "import json,sys;print(json.load(sys.stdin)$1)"; }

echo "== 1. --help =="
OUT="$("$SUT" --help 2>&1)"; RC=$?
check "--help exits 0" "[ $RC -eq 0 ]"
check "--help mentions --fix" "grep -q -- '--fix' <<<\"\$OUT\""
check "--help mentions --strict" "grep -q -- '--strict' <<<\"\$OUT\""

echo "== 2. missing DB =="
OUT="$("$SUT" --db "$TMP/nope.db" 2>&1)"; RC=$?
check "missing DB exits 2" "[ $RC -eq 2 ]"
check "missing DB prints ERROR" "grep -q 'ERROR' <<<\"\$OUT\""

echo "== 3. DB without kanban_columns =="
DB="$TMP/empty.db"; rm -f "$DB"; python3 -c "import sqlite3,sys;sqlite3.connect(sys.argv[1]).close()" "$DB"
OUT="$("$SUT" --db "$DB")"; RC=$?
check "no-table exits 0" "[ $RC -eq 0 ]"
check "no-table reports 0 invalid" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 4. clean board =="
DB="$TMP/clean.db"; seed_db "$DB"
col "$DB" c1 engineer minimax MiniMax-M3
col "$DB" c2 analyst anthropic opus
OUT="$("$SUT" --db "$DB")"; RC=$?
check "clean exits 0" "[ $RC -eq 0 ]"
check "clean reports 0 invalid" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"
check "clean scanned 2 columns" "[ \"\$(jqf \"['totals']['columns_scanned']\" <<<\"\$OUT\")\" = 2 ]"

echo "== 5. (minimax, opus) — the reported bug =="
DB="$TMP/stale.db"; seed_db "$DB"
col "$DB" c1 engineer minimax opus
OUT="$("$SUT" --db "$DB")"
check "one invalid row" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 1 ]"
check "row names the column" "[ \"\$(jqf \"['rows'][0]['name']\" <<<\"\$OUT\")\" = engineer ]"
check "reason names the model" "grep -q \"'opus'\" <<<\"\$OUT\""
check "reason names the provider" "grep -q \"'minimax'\" <<<\"\$OUT\""
check "by_provider counts minimax" "[ \"\$(jqf \"['totals']['by_provider']['minimax']\" <<<\"\$OUT\")\" = 1 ]"

echo "== 6. (anthropic, MiniMax-M3) — symmetric =="
DB="$TMP/sym.db"; seed_db "$DB"
col "$DB" c1 analyst anthropic MiniMax-M3
OUT="$("$SUT" --db "$DB")"
check "symmetric hit reported" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 1 ]"

echo "== 7. bedrock is skipped =="
DB="$TMP/bedrock.db"; seed_db "$DB"
col "$DB" c1 engineer bedrock "anthropic.claude-3-sonnet-20240229-v1:0"
OUT="$("$SUT" --db "$DB")"
check "bedrock ARN not a hit" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 8. null sides are skipped =="
DB="$TMP/nulls.db"; seed_db "$DB"
col "$DB" c1 engineer minimax ""
col "$DB" c2 analyst "" opus
col "$DB" c3 reviewer "" ""
OUT="$("$SUT" --db "$DB")"
check "null model/provider not hits" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 9. cache is respected over the seed =="
DB="$TMP/cache.db"; seed_db "$DB"
meta "$DB" "model_options:minimax" '["MiniMax-M3", "MiniMax-M2.7"]'
col "$DB" c1 engineer minimax MiniMax-M2.7
OUT="$("$SUT" --db "$DB")"
check "cached model is valid" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 10. missing/empty cache falls back to the seed =="
DB="$TMP/seed.db"; seed_db "$DB"
meta "$DB" "model_options:minimax" '[]'
col "$DB" c1 engineer minimax MiniMax-M3
col "$DB" c2 analyst anthropic sonnet
OUT="$("$SUT" --db "$DB")"
check "empty cache uses seed, not 'all invalid'" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 11. --strict =="
DB="$TMP/strict.db"; seed_db "$DB"
col "$DB" c1 engineer minimax opus
"$SUT" --db "$DB" --strict >/dev/null 2>&1; RC=$?
check "--strict with hits exits 1" "[ $RC -eq 1 ]"
"$SUT" --db "$DB" >/dev/null 2>&1; RC=$?
check "advisory with hits exits 0" "[ $RC -eq 0 ]"
DB2="$TMP/strictclean.db"; seed_db "$DB2"; col "$DB2" c1 engineer minimax MiniMax-M3
"$SUT" --db "$DB2" --strict >/dev/null 2>&1; RC=$?
check "--strict clean exits 0" "[ $RC -eq 0 ]"

echo "== 12. --fix =="
DB="$TMP/fix.db"; seed_db "$DB"
col "$DB" c1 engineer minimax opus
col "$DB" c2 analyst anthropic sonnet
"$SUT" --db "$DB" >/dev/null
check "read-only run leaves the row alone" "[ \"\$(col_field \"\$DB\" c1 default_model)\" = opus ]"
OUT="$("$SUT" --db "$DB" --fix)"
check "--fix reports the row as fixed" "[ \"\$(jqf \"['rows'][0]['fixed']\" <<<\"\$OUT\")\" = True ]"
check "--fix clears default_model" "[ \"\$(col_field \"\$DB\" c1 default_model)\" = NULL ]"
check "--fix keeps default_provider" "[ \"\$(col_field \"\$DB\" c1 default_provider)\" = minimax ]"
check "--fix leaves the valid column alone" "[ \"\$(col_field \"\$DB\" c2 default_model)\" = sonnet ]"
OUT="$("$SUT" --db "$DB" --fix)"
check "--fix is idempotent" "[ \"\$(jqf \"['totals']['invalid']\" <<<\"\$OUT\")\" = 0 ]"

echo "== 13. --strict --fix =="
DB="$TMP/strictfix.db"; seed_db "$DB"
col "$DB" c1 engineer minimax opus
"$SUT" --db "$DB" --strict --fix >/dev/null 2>&1; RC=$?
check "--strict --fix exits 0 (hits resolved)" "[ $RC -eq 0 ]"

echo "== 14. real board reachable =="
REAL="$HOME/.claude-registry/kanban.db"
if [ -f "$REAL" ]; then
  "$SUT" --db "$REAL" >/dev/null 2>&1; RC=$?
  check "real board sweeps without error" "[ $RC -eq 0 ]"
else
  ok "real board absent — skipped"
fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
