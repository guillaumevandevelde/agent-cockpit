#!/usr/bin/env bash
# Harnas voor scripts/check-file-size-ratchet.sh
#
# De asserties toetsen de EXACTE regel die het script in de schone toestand
# uitzendt, niet een patroon dat in beide toestanden slaagt. Een vorm als
# `grep -qE "^OK:|WAARSCHUWING:"` zou hier groen zijn bij een kapot script —
# precies de val uit kanban-kaart e5136a3f. Elke fout-verwachting toetst
# bovendien de exit-status én de specifieke melding.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUT="$REPO_ROOT/scripts/check-file-size-ratchet.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP" 2>/dev/null || mv "$TMP" "$TMP.stale-$$" 2>/dev/null || true' EXIT

pass=0; fail=0
ok()   { echo "  ok: $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL: $1" >&2; fail=$((fail + 1)); }

# Een baseline die precies één bestand bewaakt, met een onmogelijk lage waarde:
# elk echt bestand is groter, dus de groei-tak moet vuren.
# Drempel op 5000 zodat ALLEEN dispatch.py (10.110 regels) erboven zit; het
# op een na grootste bestand is 2620 regels. Zonder die isolatie vallen de
# andere twintig grote bestanden in de "nieuw boven drempel"-tak en slaagt
# elke fout-verwachting om de verkeerde reden.
export FILE_SIZE_THRESHOLD=5000
GUARDED="backend/app/kanban/dispatch.py"
ACTUAL=$(grep -c '' "$REPO_ROOT/$GUARDED")

# --- 1. schone toestand -----------------------------------------------------
printf '%s %s\n' "$ACTUAL" "$GUARDED" > "$TMP/exact.baseline"
out=$(FILE_SIZE_BASELINE="$TMP/exact.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && grep -qE '^OK: geen enkel bewaakt bestand is gegroeid' <<< "$out"; then
    ok "exacte baseline -> exit 0 met de schone-toestandsregel"
else
    bad "exacte baseline gaf rc=$rc: $out"
fi

# --- 2. groei wordt betrapt -------------------------------------------------
printf '%s %s\n' "$((ACTUAL - 10))" "$GUARDED" > "$TMP/grown.baseline"
out=$(FILE_SIZE_BASELINE="$TMP/grown.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && grep -qF "GROEI: $GUARDED is $ACTUAL regels, baseline $((ACTUAL - 10)) (+10)" <<< "$out"; then
    ok "groei -> exit 1 met het exacte aantal regels erbij"
else
    bad "groei niet betrapt (rc=$rc): $out"
fi

# --- 3. krimp mag ------------------------------------------------------------
printf '%s %s\n' "$((ACTUAL + 50))" "$GUARDED" > "$TMP/shrunk.baseline"
out=$(FILE_SIZE_BASELINE="$TMP/shrunk.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 0 ] && grep -qE '^OK: geen enkel bewaakt bestand is gegroeid \(1 bewaakt, 1 gekrompen\)' <<< "$out"; then
    ok "krimp -> exit 0 en wordt als gekrompen geteld"
else
    bad "krimp afgekeurd of verkeerd geteld (rc=$rc): $out"
fi

# --- 4. nieuw bestand boven de drempel --------------------------------------
: > "$TMP/empty.baseline"
out=$(FILE_SIZE_BASELINE="$TMP/empty.baseline" "$SUT" 2>&1); rc=$?
if [ "$rc" -eq 1 ] && grep -qF "NIEUW BOVEN DREMPEL: $GUARDED" <<< "$out"; then
    ok "onbekend bestand boven de drempel -> exit 1"
else
    bad "onbekend bestand boven de drempel niet gemeld (rc=$rc)"
fi

# --- 5. --update legt groei NIET vast (geen achterdeur) ---------------------
printf '%s %s\n' "$((ACTUAL - 10))" "$GUARDED" > "$TMP/backdoor.baseline"
out=$(FILE_SIZE_BASELINE="$TMP/backdoor.baseline" "$SUT" --update 2>&1); rc=$?
recorded=$(awk -v f="$GUARDED" '!/^#/ && $2 == f {print $1}' "$TMP/backdoor.baseline")
if [ "$rc" -eq 1 ] && [ "$recorded" = "$((ACTUAL - 10))" ]; then
    ok "--update weigert groei te zegenen en houdt de oude waarde ($recorded)"
else
    bad "--update zegende groei: rc=$rc, vastgelegd=$recorded (verwacht $((ACTUAL - 10)))"
fi

# --- 6. --update legt krimp WEL vast ----------------------------------------
printf '%s %s\n' "$((ACTUAL + 50))" "$GUARDED" > "$TMP/record.baseline"
FILE_SIZE_BASELINE="$TMP/record.baseline" "$SUT" --update >/dev/null 2>&1
recorded=$(awk -v f="$GUARDED" '!/^#/ && $2 == f {print $1}' "$TMP/record.baseline")
if [ "$recorded" = "$ACTUAL" ]; then
    ok "--update schuift de baseline omlaag naar $ACTUAL"
else
    bad "--update legde krimp niet vast: $recorded"
fi

echo ""
echo "Total: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
