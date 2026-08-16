#!/usr/bin/env bash
# Harnas voor scripts/sweep_self_improve_effect_claims.py.
#
# Contract: Done-kaarten met een `[self-improve]`/`[problem]`-titel of -label
# horen een effectclaim te dragen (zelfde `EFFECT_PATTERNS` als de docs-sweeper).
# Zonder claim = één rij in de JSON; --strict maakt dat exit 1.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUT="$REPO_ROOT/scripts/sweep_self_improve_effect_claims.py"
TMP="$(mktemp -d)"
trap 'mv "$TMP" "$TMP.done" 2>/dev/null || true' EXIT
DB="$TMP/kanban.db"
FAIL=0

fixture() {
    python3 - "$DB" <<'PY'
import json, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute(
    'CREATE TABLE kanban_cards (id TEXT, title TEXT, labels TEXT, '
    'project_key TEXT, description TEXT, "column" TEXT, updated_at TEXT)'
)
con.execute("CREATE TABLE kanban_ops (entity_id TEXT, op_type TEXT, payload TEXT)")
rows = [
    ("c1", "[self-improve] harnas mist claim", "[]", "p", "", "Done", "2026-08-16"),
    ("c2", "[self-improve] harnas met claim", "[]", "p", "", "Done", "2026-08-16"),
    ("c3", "[problem] via label", json.dumps(["problem"]), "p", "", "Done", "2026-08-16"),
    ("c4", "gewone feature", "[]", "p", "", "Done", "2026-08-16"),
    ("c5", "[self-improve] nog niet af", "[]", "p", "", "Backlog", "2026-08-16"),
]
con.executemany("INSERT INTO kanban_cards VALUES (?,?,?,?,?,?,?)", rows)
con.execute(
    "INSERT INTO kanban_ops VALUES (?,?,?)",
    ("c2", "comment", json.dumps({"text": "Effect: 3 logregels minder per tick."})),
)
con.execute(
    "INSERT INTO kanban_ops VALUES (?,?,?)",
    ("c3", "comment", json.dumps({"text": "**Summary:** iets gedaan."})),
)
con.commit()
PY
}

check() {  # naam, verwachte-exitcode, werkelijke-exitcode
    if [ "$2" != "$3" ]; then
        echo "FAIL: $1 (verwacht exit $2, kreeg $3)"
        FAIL=1
    else
        echo "OK: $1"
    fi
}

fixture

OUT="$("$SUT" --db "$DB")"; RC=$?
check "advisory-modus exit 0" 0 "$RC"

MISSING=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["totals"]["without_effect"])')
IDS=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(",".join(sorted(r["card_id"] for r in json.load(sys.stdin)["rows"])))')
check "twee kaarten zonder effectclaim" "2" "$MISSING"
check "precies c1 en c3 gevlagd" "c1,c3" "$IDS"

WITH=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["totals"]["with_effect"])')
check "kaart met Effect-zin telt als schoon" "1" "$WITH"

"$SUT" --db "$DB" --strict >/dev/null; RC=$?
check "--strict exit 1 bij hits" 1 "$RC"

"$SUT" --db "$TMP/bestaat-niet.db" >/dev/null 2>&1; RC=$?
check "ontbrekende DB exit 2" 2 "$RC"

# Schone DB: geen Done-loopkaarten, dus geen hits en exit 0 ook met --strict.
python3 - "$TMP/leeg.db" <<'PY'
import sqlite3, sys
sqlite3.connect(sys.argv[1]).execute("CREATE TABLE ignored (x)")
PY
"$SUT" --db "$TMP/leeg.db" --strict >/dev/null; RC=$?
check "DB zonder kanban_cards is schoon" 0 "$RC"

[ "$FAIL" = 0 ] && echo "ALL PASS"
exit "$FAIL"
