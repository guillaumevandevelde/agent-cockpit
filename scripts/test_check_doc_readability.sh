#!/usr/bin/env bash
# Test harness for scripts/check-doc-readability.py.
#
# Exercises the readability norms from docs/cockpit/taalgebruik-conventies.md
# against synthetic fixture dirs:
#
#   1. arg parsing — `--help` works, unknown flag exits 2.
#   2. clean case — short sentences, short paragraphs, no hybrid verbs.
#   3. long sentence — a >40-word sentence is reported (advisory, exit 0).
#   4. long paragraph — a >150-word paragraph is reported; a long BULLET LIST
#      of short items is NOT (the block-detection regression).
#   5. hybrid verbs — a curated hit is reported with its Dutch replacement;
#      domain jargon (dispatchen/claimen/mergen) is NOT a hit.
#   6. code and tables — prose inside fences, inline code and table rows is skipped.
#   7. --strict — the same drift exits 1.
#   8. --json — machine-readable shape carries totals + norms.
#   9. --file — per-line output carries file:line refs.
#  10. error paths — missing dir / missing --file target exit 2.
#  11. real tree — docs/cockpit is measurable and the doc's own numbers reproduce.
#
# Note on assertions: the clean-state check asserts the SPECIFIC clean line
# ("OK: alle N documenten halen de leesbaarheidsnorm.") rather than a
# `grep -qE "^OK:|WARNING:"` that would pass in both the broken and the fixed
# state (see the authoring note in CLAUDE.md's # Test block).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SUT="$SCRIPT_DIR/check-doc-readability.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing"
out=$(python3 "$SUT" --help 2>&1 || true)
check "--help mentions Gebruik" 'echo "$out" | grep -qE "Gebruik:|usage:"'
check "--help mentions --strict" 'echo "$out" | grep -q -- "--strict"'
python3 "$SUT" --bogus-flag >/dev/null 2>&1; rc=$?
check "unknown flag exits 2" '[ "$rc" = "2" ]'

# ----------------------------------------------------------------------------
echo "Task 2: clean case"
mkdir -p "$TMP/clean"
cat > "$TMP/clean/a.md" <<'EOF'
---
title: "Schoon document"
type: reference
status: active
---

# Schoon document

Dit is een korte zin. De volgende zin is ook kort en helder.

De dispatcher claimt een kaart en start een sessie. Dat is het hele verhaal.
EOF
out=$(DOCS_DIR="$TMP/clean" python3 "$SUT" 2>&1); rc=$?
check "clean case exits 0" '[ "$rc" = "0" ]'
check "clean case prints the specific OK line" 'echo "$out" | grep -qE "^OK: alle 1 documenten halen de leesbaarheidsnorm\.$"'
check "clean case has no WARNING" '! echo "$out" | grep -q "WARNING:"'
out_strict=$(DOCS_DIR="$TMP/clean" python3 "$SUT" --strict 2>&1); rc=$?
check "clean case is green under --strict too" '[ "$rc" = "0" ]'

# ----------------------------------------------------------------------------
echo "Task 3: long sentence"
mkdir -p "$TMP/longsent"
{
  printf '# Lange zin\n\n'
  printf 'Deze zin is opzettelijk veel te lang gemaakt'
  for _ in $(seq 1 45); do printf ' en gaat nog even door'; done
  printf '.\n'
} > "$TMP/longsent/a.md"
out=$(DOCS_DIR="$TMP/longsent" python3 "$SUT" 2>&1); rc=$?
check "long sentence is advisory (exit 0)" '[ "$rc" = "0" ]'
check "long sentence is reported by name" 'echo "$out" | grep -q "lange zinnen: 1"'
check "long sentence does not print the OK line" '! echo "$out" | grep -q "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 4: long paragraph vs. long bullet list"
mkdir -p "$TMP/para" "$TMP/bullets"
{
  printf '# Lange alinea\n\n'
  for _ in $(seq 1 60); do printf 'Korte zin hier. '; done
  printf '\n'
} > "$TMP/para/a.md"
out=$(DOCS_DIR="$TMP/para" python3 "$SUT" 2>&1)
check "a >150-word paragraph is reported" 'echo "$out" | grep -q "lange alinea.s: 1"'
{
  printf '# Lijst met korte items\n\n'
  for i in $(seq 1 60); do printf -- '- Item %s is kort en helder geschreven.\n' "$i"; done
} > "$TMP/bullets/a.md"
out=$(DOCS_DIR="$TMP/bullets" python3 "$SUT" 2>&1); rc=$?
check "a long list of short items is NOT a long paragraph" '[ "$rc" = "0" ] && echo "$out" | grep -qE "^OK: alle 1 documenten"'

# ----------------------------------------------------------------------------
echo "Task 5: hybrid verbs vs. domain jargon"
mkdir -p "$TMP/hybrid" "$TMP/jargon"
cat > "$TMP/hybrid/a.md" <<'EOF'
# Hybride werkwoorden

Het patroon globt tegen de map. Het script flag't de afwijking.
EOF
out=$(DOCS_DIR="$TMP/hybrid" python3 "$SUT" --file "$TMP/hybrid/a.md" 2>&1)
check "hybrid verb 'globt' is reported" 'echo "$out" | grep -q "hybrid_verb.*globt"'
check "hybrid verb carries a Dutch replacement" 'echo "$out" | grep -q "matcht als glob-patroon"'
check "hybrid verb 'flag.t' is reported" "echo \"\$out\" | grep -q \"flag't\""
cat > "$TMP/jargon/a.md" <<'EOF'
# Domeinjargon

De dispatcher claimt de kaart en gaat dispatchen. Daarna mergen we de branch.
Een sessie shippen en spawnen hoort bij het domein.
EOF
out=$(DOCS_DIR="$TMP/jargon" python3 "$SUT" 2>&1); rc=$?
check "domain jargon is not a violation" '[ "$rc" = "0" ] && echo "$out" | grep -qE "^OK: alle 1 documenten"'

# ----------------------------------------------------------------------------
echo "Task 6: code, inline code and tables are skipped"
mkdir -p "$TMP/skip"
{
  printf '# Overslaan\n\n'
  printf '| kolom | betekenis |\n|---|---|\n'
  printf '| een lange tabelrij met heel veel woorden die samen ruim boven de veertig woorden uitkomen en dus zou tellen als een lange zin als tabellen niet werden overgeslagen | data |\n\n'
  printf '```bash\n'
  printf 'echo "een heel lang codecommentaar dat als proza zou tellen wanneer fenced code niet werd overgeslagen en dat is precies wat we hier controleren want anders vervuilt code de meting volledig"\n'
  printf '```\n\n'
  printf 'Korte zin buiten de code.\n'
} > "$TMP/skip/a.md"
out=$(DOCS_DIR="$TMP/skip" python3 "$SUT" 2>&1); rc=$?
check "tables and fenced code are not measured" '[ "$rc" = "0" ] && echo "$out" | grep -qE "^OK: alle 1 documenten"'

# ----------------------------------------------------------------------------
echo "Task 7: --strict"
out=$(DOCS_DIR="$TMP/longsent" python3 "$SUT" --strict 2>&1); rc=$?
check "--strict exits 1 on the same drift" '[ "$rc" = "1" ]'

# ----------------------------------------------------------------------------
echo "Task 8: --json"
out=$(DOCS_DIR="$TMP/longsent" python3 "$SUT" --json 2>/dev/null)
check "--json is valid JSON" 'echo "$out" | python3 -c "import json,sys; json.load(sys.stdin)"'
check "--json carries the long_sentence total" 'echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d[\"totals\"][\"long_sentence\"]==1 else 1)"'
check "--json carries the norms" 'echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d[\"norms\"][\"max_sentence_words\"]==40 else 1)"'
check "--json carries per-hit line numbers" 'echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d[\"reports\"][0][\"hits\"][0][\"line\"]>0 else 1)"'

# ----------------------------------------------------------------------------
echo "Task 9: --file gives file:line refs"
out=$(python3 "$SUT" --file "$TMP/longsent/a.md" 2>/dev/null)
check "--file output carries a file:line ref" 'echo "$out" | grep -qE "a\.md:[0-9]+: long_sentence"'

# ----------------------------------------------------------------------------
echo "Task 10: error paths"
DOCS_DIR="$TMP/bestaat-niet" python3 "$SUT" >/dev/null 2>&1; rc=$?
check "missing docs dir exits 2" '[ "$rc" = "2" ]'
python3 "$SUT" --file "$TMP/nergens.md" >/dev/null 2>&1; rc=$?
check "missing --file target exits 2" '[ "$rc" = "2" ]'

# ----------------------------------------------------------------------------
echo "Task 11: real tree"
out=$(cd "$REPO_ROOT" && python3 "$SUT" --json 2>/dev/null)
check "real docs/cockpit tree parses" 'echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d[\"files\"]>50 else 1)"'
check "the convention doc itself meets the norm" 'echo "$out" | python3 -c "
import json,sys
d=json.load(sys.stdin)
own=[r for r in d[\"reports\"] if r[\"path\"].endswith(\"taalgebruik-conventies.md\")]
raise SystemExit(0 if own and own[0][\"violations\"]==0 else 1)"'

# ----------------------------------------------------------------------------
echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ]
