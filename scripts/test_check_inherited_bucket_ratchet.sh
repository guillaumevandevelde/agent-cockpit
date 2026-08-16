#!/usr/bin/env bash
# Harnas voor scripts/check-inherited-bucket-ratchet.sh
#
# De asserties toetsen de EXACTE regel die het script in de schone toestand
# uitzendt, niet een patroon dat in beide toestanden slaagt. Een vorm als
# `grep -qE "^OK:|WAARSCHUWING:"` zou hier groen zijn bij een kapot script —
# precies de val uit kanban-kaart e5136a3f. Elke fout-verwachting toetst
# bovendien de exit-status én de specifieke melding.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUT="$REPO_ROOT/scripts/check-inherited-bucket-ratchet.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP" 2>/dev/null || mv "$TMP" "$TMP.stale-$$" 2>/dev/null || true' EXIT

pass=0; fail=0
ok()   { echo "  ok: $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL: $1" >&2; fail=$((fail + 1)); }

# Kies één map uit de geconfigureerde INHERITED_DIRS-lijst zodat de asserts
# deterministisch landen — `updates` is de kleinste map (één bestand) en
# gegarandeerd in de bucket (cockpit-richting-decision.md §3).
TARGET="updates"
TARGET_PATH="frontend/src/features/$TARGET"
# `2>/dev/null` dempt de ENOENT-watmerking wanneer $TARGET_PATH weg is (kaart
# 8b1bd6bcf2244809b283696b90eef20c verwijderde features/updates/); anders zou
# compare-bash-tests.sh die regel als "harness crashed" classificeren.
ACTUAL=$(find "$REPO_ROOT/$TARGET_PATH" -type f \( -name "*.ts" -o -name "*.tsx" \) ! -path "*/node_modules/*" -print0 2>/dev/null \
    | xargs -0 grep -c '' 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}')

# Maak een fixture-baseline waarin ELKE van de 19 mappen voorkomt met zijn
# werkelijke LoC. Anders zou de SUT voor de overige 18 "NIEUW IN BUCKET"
# roepen en de exacte-baseline-assert verwateren. Voor TARGET schrijven de
# afzonderlijke tests een afwijkende waarde — vandaar de override-route.
make_fixture() {  # $1 = override-waarde voor TARGET (leeg = exact)
    local actual_val="${1:-$ACTUAL}"
    for d in commands hooks permissions plugins mcp mcp-server output-styles statusline skills memory config updates security endpoints subscriptions usage context backup blueprints; do
        local n
        if [ "$d" = "$TARGET" ]; then
            n="$actual_val"
        else
            n=$(find "$REPO_ROOT/frontend/src/features/$d" -type f \( -name "*.ts" -o -name "*.tsx" \) ! -path "*/node_modules/*" -print0 2>/dev/null \
                | xargs -0 grep -c '' 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}')
        fi
        printf '%s %s\n' "$n" "$d"
    done
}

# --- 1. schone toestand met exacte baseline -------------------------------
make_fixture > "$TMP/exact.baseline"
out=$(INHERITED_BUCKET_BASELINE="$TMP/exact.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && grep -qE '^OK: geen enkele bewaakte geërfde map is gegroeid \(19 bewaakt, 0 gekrompen\)' <<< "$out"; then
    ok "exacte baseline -> exit 0 met de schone-toestandsregel"
else
    bad "exacte baseline gaf rc=$rc: $out"
fi

# --- 2. groei wordt betrapt -------------------------------------------------
make_fixture $((ACTUAL - 10)) > "$TMP/grown.baseline"
out=$(INHERITED_BUCKET_BASELINE="$TMP/grown.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && grep -qF "GROEI: $TARGET_PATH is $ACTUAL regels, baseline $((ACTUAL - 10)) (+10)" <<< "$out"; then
    ok "groei -> exit 1 met het exacte aantal regels erbij"
else
    bad "groei niet betrapt (rc=$rc): $out"
fi

# --- 3. krimp mag -----------------------------------------------------------
make_fixture $((ACTUAL + 50)) > "$TMP/shrunk.baseline"
out=$(INHERITED_BUCKET_BASELINE="$TMP/shrunk.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && grep -qE '^OK: geen enkele bewaakte geërfde map is gegroeid \(19 bewaakt, 1 gekrompen\)' <<< "$out"; then
    ok "krimp -> exit 0 en wordt als gekrompen geteld"
else
    bad "krimp afgekeurd of verkeerd geteld (rc=$rc): $out"
fi

# --- 4. nieuwe map zonder baseline -----------------------------------------
# Fixture met 18 mappen, $TARGET eruit — script moet die als NIEUW melden.
make_fixture | grep -v "^$ACTUAL $TARGET$" > "$TMP/missing.baseline"
out=$(INHERITED_BUCKET_BASELINE="$TMP/missing.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && grep -qF "NIEUW IN BUCKET: $TARGET_PATH" <<< "$out"; then
    ok "onbekende map -> exit 1 met de bucketnaam"
else
    bad "onbekende map niet gemeld (rc=$rc)"
fi

# --- 5. --update legt groei NIET vast (geen achterdeur) --------------------
make_fixture $((ACTUAL - 10)) > "$TMP/backdoor.baseline"
out=$(INHERITED_BUCKET_BASELINE="$TMP/backdoor.baseline" "$SUT" --update 2>&1); rc=$?
recorded=$(awk -v f="$TARGET" '!/^#/ && $2 == f {print $1}' "$TMP/backdoor.baseline")
if [ "$rc" -eq 1 ] && [ "$recorded" = "$((ACTUAL - 10))" ]; then
    ok "--update weigert groei te zegenen en houdt de oude waarde ($recorded)"
else
    bad "--update zegende groei: rc=$rc, vastgelegd=$recorded (verwacht $((ACTUAL - 10)))"
fi

# --- 6. --update legt krimp WEL vast ----------------------------------------
make_fixture $((ACTUAL + 50)) > "$TMP/record.baseline"
INHERITED_BUCKET_BASELINE="$TMP/record.baseline" "$SUT" --update >/dev/null 2>&1
recorded=$(awk -v f="$TARGET" '!/^#/ && $2 == f {print $1}' "$TMP/record.baseline")
if [ "$recorded" = "$ACTUAL" ]; then
    ok "--update schuift de baseline omlaag naar $ACTUAL"
else
    bad "--update legde krimp niet vast: $recorded"
fi

# --- 7. --update registreert een nieuwe map --------------------------------
make_fixture | grep -v "^$ACTUAL $TARGET$" > "$TMP/firsttime.baseline"
INHERITED_BUCKET_BASELINE="$TMP/firsttime.baseline" "$SUT" --update >/dev/null 2>&1
recorded=$(awk -v f="$TARGET" '!/^#/ && $2 == f {print $1}' "$TMP/firsttime.baseline")
if [ "$recorded" = "$ACTUAL" ]; then
    ok "--update legt eerste baseline vast voor een onbekende map ($recorded)"
else
    bad "--update legde eerste baseline niet vast: $recorded"
fi

echo ""
echo "Total: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
