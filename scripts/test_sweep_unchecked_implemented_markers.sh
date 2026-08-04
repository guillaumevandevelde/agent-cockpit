#!/usr/bin/bash
# Test harness for scripts/sweep_unchecked_implemented_markers.py.
#
# Exercises the marker-sweep against a synthetic docs-fixture in a tempdir.
# The real-docs check is a final optional task — production analysedocs are
# expected to violate the new convention until authors add effect-claims,
# so a smoke-test on the live tree would be flaky by design.
#
# The sweep flags every `✅ Geïmplementeerd` / `✅ Uitgevoerd` marker in
# `docs/cockpit/*.md` whose next 3 lines contain no effect-claim
# (`Effect:`, "logregels", "nog niet in productie waargenomen", ...).
# A marker with an effect-claim within the window is silently omitted.
#
# Tasks covered:
#   1.  --help runs and lists the synopsis.
#   2.  error — missing docs-dir → exit 2 + ERROR on stderr.
#   3.  clean fixture — no markers → totals all zero, exit 0.
#   4.  one marker without effect → exactly one row, snippet + line surfaced.
#   5.  one marker with effect-claim → not reported at all.
#   6.  blank line STOPS the window — Effect in next alinea NOT matched.
#   7.  same-paragraph, Effect 4 lines later → matched (no paragraph break).
#   8.  mixed file — 1 bad + 1 good marker → only the bad one reported.
#   9.  --strict with hits → exit 1; --strict clean → exit 0.
#  10.  marker_kind correct: 'geimplementeerd' vs 'uitgevoerd'.
#  11.  marker without `kaart` reference (heading / blockquote-meta) → skipped.
#  12.  marker inside fenced code block → skipped.
#  13.  backtick-wrapped marker → skipped.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_unchecked_implemented_markers.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
title(){ printf '\n== %s ==\n' "$1"; }

# ---------- 1. --help ----------
title "--help lists the synopsis"
HELP_OUT=$(python3 "$SUT" --help 2>&1) || true
if printf '%s' "$HELP_OUT" | grep -q "Sweep unmarked" \
  && printf '%s' "$HELP_OUT" | grep -q -- "--strict"; then
  ok "--help exposes synopsis + --strict"
else
  bad "--help missing expected strings"
  printf '%s\n' "$HELP_OUT" | head -5
fi

# ---------- 2. missing docs-dir ----------
title "missing docs-dir → exit 2"
set +e
ERR=$(python3 "$SUT" --docs-dir /no/such/path/that/should/exist 2>&1)
RC=$?
set -e
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "docs-dir niet gevonden"; then
  ok "missing docs-dir returns 2 + ERROR message"
else
  bad "expected exit 2 + ERROR; got rc=$RC"
  printf '%s\n' "$ERR" | head -3
fi

# ---------- 3..9 fixtures ----------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Clean fixture
mkdir -p "$TMP/clean"
cat > "$TMP/clean/nothing.md" <<'MD'
---
title: leeg
type: analysis
status: active
---
# Leeg
Geen markers hier.
MD

# One bad marker
mkdir -p "$TMP/onebad"
cat > "$TMP/onebad/x.md" <<'MD'
# X
✅ Geïmplementeerd (kaart `abc12345…`): de detector draait elke tick.
MD

# One good marker
mkdir -p "$TMP/onegood"
cat > "$TMP/onegood/x.md" <<'MD'
# X
✅ Geïmplementeerd (kaart `abc12345…`): de detector draait elke tick.
Effect: 12 logregels in 18 dagen productie.
MD

# Blank-line STOPS the window (conventie: Effect hoort in dezelfde alinea)
mkdir -p "$TMP/blank-window"
cat > "$TMP/blank-window/x.md" <<'MD'
# X
✅ Geïmplementeerd (kaart `abc12345…`): de detector draait elke tick.

Effect: 0 logregels — detector sluit doel-verzameling uit.
MD

# Same-paragraph, 4 lines later — SHOULD match (same paragraph, no break)
mkdir -p "$TMP/same-paragraph"
cat > "$TMP/same-paragraph/x.md" <<'MD'
# X
✅ Geïmplementeerd (kaart `abc12345…`): de detector draait elke tick.
Een uitleg die het effect-claim-patroon niet matcht.
Nog een tussenregel.
Nog een tussenregel.
Effect: 12 logregels in 18 dagen productie.
MD

# Mixed file (markers separated by a heading so the effect-claim clearly
# belongs to the second marker, not the first)
mkdir -p "$TMP/mixed"
cat > "$TMP/mixed/x.md" <<'MD'
# X

## Eerste aanbeveling

✅ Geïmplementeerd (kaart `aaa11111…`): zonder effect.

## Tweede aanbeveling

✅ Geïmplementeerd (kaart `bbb22222…`): met effect.
Effect: gemeten gedragsverandering — false-positive 6/8 → 0/8.
MD

# Uitgevoerd kind
mkdir -p "$TMP/uitgevoerd"
cat > "$TMP/uitgevoerd/x.md" <<'MD'
# X
✅ Uitgevoerd (kaart `ccc33333…`): geen effect.
MD

# Marker without kaart reference (heading / blockquote-meta) — should be
# silently skipped, not reported as effect-less.
mkdir -p "$TMP/no-card-ref"
cat > "$TMP/no-card-ref/x.md" <<'MD'
# X

## 2. "✅ Geïmplementeerd" in analysedocs = code gemerged, niet gat gedicht

> ✅ Geïmplementeerd-patroon in `docs/cockpit/*-analyse.md`;
> **Een `✅ Geïmplementeerd`-regel in een analysedoc is pas geldig als
> ernaast een waargenomen effect staat**.

✅ Geïmplementeerd (kaart `ddd44444…`): echte marker, geen effect.
MD

# ---------- 3. clean ----------
title "clean fixture → totals all zero"
OUT=$(python3 "$SUT" --docs-dir "$TMP/clean" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==0; assert r['totals']['markers_without_effect']==0; assert r['rows']==[]; print('ok')" 2>/dev/null | grep -q ok; then
  ok "clean fixture reports 0 markers / 0 rows"
else
  bad "clean fixture should report 0"
  printf '%s\n' "$OUT" | head -10
fi

# ---------- 4. one bad ----------
title "one bad marker → exactly one row"
OUT=$(python3 "$SUT" --docs-dir "$TMP/onebad" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==1; assert r['totals']['markers_with_effect']==0; assert r['totals']['markers_without_effect']==1; assert len(r['rows'])==1; assert r['rows'][0]['marker_kind']=='geimplementeerd'; assert r['rows'][0]['line']==2; print('ok')" 2>/dev/null | grep -q ok; then
  ok "one bad marker reported with marker_kind=geimplementeerd"
else
  bad "onebad script mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 5. one good ----------
title "one good marker → not reported"
OUT=$(python3 "$SUT" --docs-dir "$TMP/onegood" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==1; assert r['totals']['markers_with_effect']==1; assert r['totals']['markers_without_effect']==0; assert r['rows']==[]; print('ok')" 2>/dev/null | grep -q ok; then
  ok "good marker counted as with_effect, no row"
else
  bad "onegood mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 6. blank-line stops window ----------
title "blank-line STOPS the window (Effect in volgende alinea niet gematcht)"
OUT=$(python3 "$SUT" --docs-dir "$TMP/blank-window" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_with_effect']==0; assert r['totals']['markers_without_effect']==1; print('ok')" 2>/dev/null | grep -q ok; then
  ok "blank-line stops window — Effect in volgende alinea wordt NIET gematcht"
else
  bad "blank-window mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 7. same-paragraph, 4 lines later ----------
title "same-paragraph, Effect 4 lines later → matched"
OUT=$(python3 "$SUT" --docs-dir "$TMP/same-paragraph" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_with_effect']==1; assert r['totals']['markers_without_effect']==0; print('ok')" 2>/dev/null | grep -q ok; then
  ok "same-paragraph: Effect 4 lines later IS matched"
else
  bad "same-paragraph mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 8. mixed file ----------
title "mixed file → only bad marker reported"
OUT=$(python3 "$SUT" --docs-dir "$TMP/mixed" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==2; assert r['totals']['markers_with_effect']==1; assert r['totals']['markers_without_effect']==1; assert len(r['rows'])==1; assert 'aaa11111' in r['rows'][0]['snippet']; print('ok')" 2>/dev/null | grep -q ok; then
  ok "mixed file reports only the bad marker"
else
  bad "mixed mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 10. Uitgevoerd kind ----------
title "Uitgevoerd marker classified correctly"
OUT=$(python3 "$SUT" --docs-dir "$TMP/uitgevoerd" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_without_effect']==1; assert r['rows'][0]['marker_kind']=='uitgevoerd'; print('ok')" 2>/dev/null | grep -q ok; then
  ok "Uitgevoerd reports marker_kind=uitgevoerd"
else
  bad "uitgevoerd mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 9. --strict ----------
title "--strict vs advisory exit codes"
set +e
( python3 "$SUT" --docs-dir "$TMP/onebad" >/dev/null 2>&1 )
RC_DEFAULT=$?
( python3 "$SUT" --docs-dir "$TMP/onebad" --strict >/dev/null 2>&1 )
RC_STRICT=$?
( python3 "$SUT" --docs-dir "$TMP/clean" --strict >/dev/null 2>&1 )
RC_STRICT_CLEAN=$?
set -e
if [ "$RC_DEFAULT" = "0" ] && [ "$RC_STRICT" = "1" ] && [ "$RC_STRICT_CLEAN" = "0" ]; then
  ok "--strict: 0 advisory, 1 met hit, 0 zonder hit"
else
  bad "exit codes: default=$RC_DEFAULT strict=$RC_STRICT strict-clean=$RC_STRICT_CLEAN"
fi

# ---------- 11. marker without kaart reference ----------
title "marker without kaart reference → skipped"
OUT=$(python3 "$SUT" --docs-dir "$TMP/no-card-ref" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==1; assert r['totals']['markers_without_effect']==1; assert len(r['rows'])==1; assert 'ddd44444' in r['rows'][0]['snippet']; print('ok')" 2>/dev/null | grep -q ok; then
  ok "no-card-ref-filter: only the marker WITH kaart-reference is reported"
else
  bad "no-card-ref-filter mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 12. fenced code block ----------
title "marker inside fenced code block → skipped"
mkdir -p "$TMP/fenced"
cat > "$TMP/fenced/x.md" <<'MD'
# X

```
✅ Geïmplementeerd (kaart `eee55555…`): voorbeeld in code block.
```

✅ Geïmplementeerd (kaart `fff66666…`): echte marker, geen effect.
MD
OUT=$(python3 "$SUT" --docs-dir "$TMP/fenced" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==1; assert 'fff66666' in r['rows'][0]['snippet']; print('ok')" 2>/dev/null | grep -q ok; then
  ok "fenced-code-block filter excludes the example marker"
else
  bad "fenced-code-block mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- 13. backtick-wrapped marker ----------
title "backtick-wrapped marker → skipped"
mkdir -p "$TMP/backtick"
cat > "$TMP/backtick/x.md" <<'MD'
# X

De `✅ Geïmplementeerd`-regel heeft een waargenomen effect nodig.

✅ Geïmplementeerd (kaart `ggg77777…`): echte marker, geen effect.
MD
OUT=$(python3 "$SUT" --docs-dir "$TMP/backtick" 2>&1) || true
if printf '%s' "$OUT" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['totals']['markers_total']==1; assert 'ggg77777' in r['rows'][0]['snippet']; print('ok')" 2>/dev/null | grep -q ok; then
  ok "backtick-wrapped filter excludes the prose marker"
else
  bad "backtick-wrapped mismatch"
  printf '%s\n' "$OUT" | head -20
fi

# ---------- summary ----------
printf '\n%d pass, %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
