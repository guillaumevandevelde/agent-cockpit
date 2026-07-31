#!/usr/bin/env bash
# Test harness for scripts/check-decision-register.sh.
#
# Exercises the drift check against synthetic fixture dirs (never the real
# docs/cockpit tree), so the test stays green regardless of which decision
# docs happen to exist on the branch:
#
#   1. arg parsing — `--help` works.
#   2. clean case — every *-decision.md linked from decisions.md → exit 0, "OK".
#   3. drift case — an unlinked decision doc is reported by name, exit 0 (advisory).
#   4. --strict — same drift, exit 1.
#   5. error path — missing register → exit 2.
#   6. real tree — the repo's own register has no drift.
#   7. --check-headers: --help mentions the flag.
#   8. --check-headers happy path — every doc has Datum/Status/Kaart/Uitkomst + Uitkomst matches register → exit 0.
#   9. --check-headers: missing Datum field → exit 0 with WARNING naming 'Datum'.
#  10. --check-headers: Uitkomst in doc ≠ register row → exit 0 with WARNING naming 'Uitkomst'.
#  11. --check-headers + --strict: any deviation → exit 1 (currently covers the same deviations as Tasks 9–10).
#  12. --check-headers + --strict: real docs/cockpit tree (after backfill) is clean → exit 0.
#
# The script under test reads DECISIONS_DIR from the env, defaulting to
# docs/cockpit; the fixtures below set it to a tmpdir.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/check-decision-register.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage" 'echo "$out" | grep -qE "Usage:"'
check "--help mentions --strict" 'echo "$out" | grep -qE "\-\-strict"'

# ----------------------------------------------------------------------------
echo "Task 2: clean case — all decision docs linked"
clean="$TMP/clean"; mkdir -p "$clean"
printf '# reg\n\n| d | v | u | [`a-decision.md`](./a-decision.md) | x |\n| d | v | u | [`b-decision.md`](./b-decision.md) | x |\n' > "$clean/decisions.md"
echo '# a' > "$clean/a-decision.md"
echo '# b' > "$clean/b-decision.md"
out=$(DECISIONS_DIR="$clean" bash "$SUT" 2>&1); rc=$?
check "clean → exit 0" '[ "$rc" -eq 0 ]'
check "clean → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 3: drift case — one unlinked decision doc"
drift="$TMP/drift"; mkdir -p "$drift"
printf '# reg\n\n| d | v | u | [`a-decision.md`](./a-decision.md) | x |\n' > "$drift/decisions.md"
echo '# a' > "$drift/a-decision.md"
echo '# orphan' > "$drift/orphan-decision.md"
out=$(DECISIONS_DIR="$drift" bash "$SUT" 2>&1); rc=$?
check "drift → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "drift → names the unlinked doc" 'echo "$out" | grep -qF "orphan-decision.md"'
check "drift → does not name the linked doc" '! echo "$out" | grep -qF "/a-decision.md"'
check "drift → reports exactly 1 unlinked doc" 'echo "$out" | grep -qE "WARNING: 1 decision doc"'
check "drift → prints WARNING" 'echo "$out" | grep -qE "WARNING:"'
check "drift → points at the register" 'echo "$out" | grep -qF "decisions.md"'

# ----------------------------------------------------------------------------
echo "Task 4: --strict turns drift into a failure"
out=$(DECISIONS_DIR="$drift" bash "$SUT" --strict 2>&1); rc=$?
check "drift + --strict → exit 1" '[ "$rc" -eq 1 ]'
check "drift + --strict → still names the doc" 'echo "$out" | grep -qF "orphan-decision.md"'

# ----------------------------------------------------------------------------
echo "Task 5: error path — missing register"
empty="$TMP/empty"; mkdir -p "$empty"
echo '# a' > "$empty/a-decision.md"
out=$(DECISIONS_DIR="$empty" bash "$SUT" 2>&1); rc=$?
check "missing register → exit 2" '[ "$rc" -eq 2 ]'
check "missing register → ERROR mentions decisions.md" 'echo "$out" | grep -qE "ERROR.*decisions\.md"'

# ----------------------------------------------------------------------------
echo "Task 6: the repo's own register is drift-free"
out=$(bash "$SUT" --strict 2>&1); rc=$?
check "real docs/cockpit → exit 0 under --strict" '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
# Helper: write a header-complete decision doc with a known Uitkomst.
write_full_header_doc() {
  # $1=file $2=uitkomst-text $3=extra-body (optional, defaults to a single newline)
  local f="$1" uitkomst="$2" extra="${3:-}"
  {
    echo '# Title'
    echo '**Datum:** 2026-07-14'
    echo '**Status:** besloten'
    echo '**Kaart:** `abc12345`'
    echo "**Uitkomst:** $uitkomst"
    echo
    echo '## Body'
    if [ -n "$extra" ]; then
      printf '%s\n' "$extra"
    fi
  } > "$f"
}

# ----------------------------------------------------------------------------
echo "Task 7: arg parsing — --check-headers shows in --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions --check-headers" 'echo "$out" | grep -qE "\-\-check-headers"'

# ----------------------------------------------------------------------------
echo "Task 8: --check-headers happy path — every doc carries the full header and Uitkomst matches register"
hdr="$TMP/hdr"; mkdir -p "$hdr"
{
  echo '# reg'
  echo
  echo '| d | v | **Conditionele GO** — netjes geïmplementeerd. | [`a-decision.md`](./a-decision.md) | x |'
} > "$hdr/decisions.md"
write_full_header_doc "$hdr/a-decision.md" "**Conditionele GO** — netjes geïmplementeerd."
out=$(DECISIONS_DIR="$hdr" bash "$SUT" --check-headers 2>&1); rc=$?
check "hdr happy → exit 0" '[ "$rc" -eq 0 ]'
check "hdr happy → no header warnings" '! echo "$out" | grep -qE "WARNING.*[Hh]eader"'
check "hdr happy → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 9: --check-headers catches a missing Datum field (advisory)"
miss="$TMP/miss"; mkdir -p "$miss"
{
  echo '# reg'
  echo
  echo '| d | v | u | [`a-decision.md`](./a-decision.md) | x |'
} > "$miss/decisions.md"
{
  echo '# Title'
  echo '**Status:** besloten'
  echo '**Kaart:** `abc12345`'
  echo '**Uitkomst:** **GO.**'
} > "$miss/a-decision.md"
out=$(DECISIONS_DIR="$miss" bash "$SUT" --check-headers 2>&1); rc=$?
check "miss → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "miss → WARNING names the missing field Datum" 'echo "$out" | grep -qE "WARNING.*Datum"'
check "miss → names the offending doc" 'echo "$out" | grep -qF "a-decision.md"'

# ----------------------------------------------------------------------------
echo "Task 10: --check-headers catches a Uitkomst that no longer matches the register row"
diffu="$TMP/diffu"; mkdir -p "$diffu"
{
  echo '# reg'
  echo
  echo '| d | v | u | **GO, old wording.** | [`a-decision.md`](./a-decision.md) | x |'
} > "$diffu/decisions.md"
write_full_header_doc "$diffu/a-decision.md" "**NOG NIET BESLIST** — aanbeveling, geen uitkomst."
out=$(DECISIONS_DIR="$diffu" bash "$SUT" --check-headers 2>&1); rc=$?
check "diffu → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "diffu → WARNING names the Uitkomst field" 'echo "$out" | grep -qE "WARNING.*Uitkomst"'

# ----------------------------------------------------------------------------
echo "Task 11: --check-headers --strict turns header drift into exit 1 (covers the same fixtures as Tasks 9–10)"
out=$(DECISIONS_DIR="$miss" bash "$SUT" --check-headers --strict 2>&1); rc=$?
check "miss + --strict → exit 1" '[ "$rc" -eq 1 ]'
check "miss + --strict → still names Datum" 'echo "$out" | grep -qE "WARNING.*Datum"'
out=$(DECISIONS_DIR="$diffu" bash "$SUT" --check-headers --strict 2>&1); rc=$?
check "diffu + --strict → exit 1" '[ "$rc" -eq 1 ]'
check "diffu + --strict → still names Uitkomst" 'echo "$out" | grep -qE "WARNING.*Uitkomst"'

# ----------------------------------------------------------------------------
echo "Task 12: real docs/cockpit tree has no header drift after backfill"
out=$(bash "$SUT" --check-headers --strict 2>&1); rc=$?
check "real tree --check-headers --strict → exit 0" '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 13: --check-headers warning count matches the listed-doc count (regression: 2x off)"
# Three docs each missing Datum → all three should be reported, and the
# summary line must say "3" (not 6). Regression for the case where the
# header_mismatches array was populated with two elements per doc, so
# ${#header_mismatches[@]} was always 2× the actual count.
multi="$TMP/multi"; mkdir -p "$multi"
{
  echo '# reg'
  echo
  echo '| d | v | u | [`a-decision.md`](./a-decision.md) | x |'
  echo '| d | v | u | [`b-decision.md`](./b-decision.md) | x |'
  echo '| d | v | u | [`c-decision.md`](./c-decision.md) | x |'
} > "$multi/decisions.md"
for n in a b c; do
  cat > "$multi/${n}-decision.md" <<EOF
# Title
**Status:** besloten
**Kaart:** \`abc12345\`
**Uitkomst:** GO.
EOF
done
out=$(DECISIONS_DIR="$multi" bash "$SUT" --check-headers 2>&1)
check "multi → WARNING count is 3 (not 6)" 'echo "$out" | grep -qE "WARNING: 3 decision doc"'
check "multi → does NOT report 6" '! echo "$out" | grep -qE "WARNING: 6 decision doc"'
check "multi → exactly 3 list lines" '[ "$(echo "$out" | grep -cE "^  - .+-decision\.md  \(")" -eq 3 ]'

# ----------------------------------------------------------------------------
echo "Task 14: --check-headers does NOT drift when Uitkomst contains a single quote (regression: eval-with-quotes strips ')"
sq="$TMP/sq"; mkdir -p "$sq"
# Register row: Uitkomst cell contains the canonical "work_type='analysis'" form
# (single quotes are routine in code-idiom Uitkomsten in this repo).
printf "%s\n%s\n%s\n" "# reg" "" "| d | v | work_type='analysis' (single quotes) | [\`a-decision.md\`](./a-decision.md) | x |" > "$sq/decisions.md"
cat > "$sq/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** work_type='analysis' (single quotes)
EOF
out=$(DECISIONS_DIR="$sq" bash "$SUT" --check-headers 2>&1); rc=$?
check "sq → exit 0" '[ "$rc" -eq 0 ]'
# CLAUDE.md gotcha: assert the EXACT clean-state line, not a permissive "^OK:|WARNING:"
# alternation that passes in both broken and fixed states.
check "sq → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'
check "sq → NO WARNING line emitted" '! echo "$out" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 15: --check-headers does NOT drift when Uitkomst contains a pipe (regression: split-on-| truncates cell)"
pipedir="$TMP/pipe"; mkdir -p "$pipedir"
# Register row: Uitkomst cell literally contains "analysis|feature|bug|chore" — the WORK_TYPES
# enum. The doc carries the same cell verbatim. The anchor-on-doc-link strategy is
# supposed to make this robust against internal `|`, but the awk still splits on `|`.
printf "%s\n%s\n%s\n" "# reg" "" "| d | v | analysis|feature|bug|chore (enum) | [\`a-decision.md\`](./a-decision.md) | x |" > "$pipedir/decisions.md"
cat > "$pipedir/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** analysis|feature|bug|chore (enum)
EOF
out=$(DECISIONS_DIR="$pipedir" bash "$SUT" --check-headers 2>&1); rc=$?
check "pipe → exit 0" '[ "$rc" -eq 0 ]'
check "pipe → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'
check "pipe → NO WARNING line emitted" '! echo "$out" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 16: --check-headers picks the row whose Kaart matches the doc's Kaart, not the first link"
# Two rows in the register point to the SAME doc but carry DIFFERENT Kaart
# columns and DIFFERENT Uitkomst cells. The doc's Kaart field is `bbbb2222…`,
# so the script must pick the second row's Uitkomst — not the first row's,
# which would also match the doc link. This is the regression that motivated
# kanban-kaart 9a2c47b1…: the previous "first-link" picker silently matched
# the wrong row the moment a doc accumulated a revision row in the register.
kaartdir="$TMP/kaart"; mkdir -p "$kaartdir"
{
  echo '# reg'
  echo
  echo '| d | v | **Original.** | [`a-decision.md`](./a-decision.md) | `aaaa1111` |'
  echo '| d | v | **Revision.** | [`a-decision.md`](./a-decision.md) | `bbbb2222` |'
} > "$kaartdir/decisions.md"
cat > "$kaartdir/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `bbbb2222`
**Uitkomst:** **Revision.**
EOF
out=$(DECISIONS_DIR="$kaartdir" bash "$SUT" --check-headers 2>&1); rc=$?
check "kaart-match → exit 0" '[ "$rc" -eq 0 ]'
# CLAUDE.md gotcha: assert the EXACT clean-state line, not a permissive "^OK:|WARNING:"
# alternation that passes in both broken and fixed states.
check "kaart-match → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'
check "kaart-match → NO WARNING line emitted" '! echo "$out" | grep -qE "WARNING:"'
# Now flip the doc's Kaart to the first row's id; the script must now pick
# the FIRST row's Uitkomst — confirming the match is by Kaart, not
# accidental row-order stability.
cat > "$kaartdir/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `aaaa1111`
**Uitkomst:** **Original.**
EOF
out=$(DECISIONS_DIR="$kaartdir" bash "$SUT" --check-headers 2>&1); rc=$?
check "kaart-match (first row) → exit 0" '[ "$rc" -eq 0 ]'
check "kaart-match (first row) → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'
check "kaart-match (first row) → NO WARNING line emitted" '! echo "$out" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 17: --check-headers falls back to first-link match when doc has no real Kaart (placeholder)"
# The "_zie doc — geen hex-id …" placeholder is the canonical form for
# legacy decisions that pre-date the four-field header convention (kaart
# 7edc5e26…). The script must NOT pick a row by some empty-prefix accident
# of the Kaart cell — it should fall through to the legacy first-link
# picker and match the first (and only) row.
nokaart="$TMP/nokaart"; mkdir -p "$nokaart"
{
  echo '# reg'
  echo
  echo '| d | v | **First.** | [`a-decision.md`](./a-decision.md) | `aaaa1111` |'
  echo '| d | v | **Second.** | [`a-decision.md`](./a-decision.md) | `bbbb2222` |'
} > "$nokaart/decisions.md"
cat > "$nokaart/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **First.**
EOF
out=$(DECISIONS_DIR="$nokaart" bash "$SUT" --check-headers 2>&1); rc=$?
check "nokaart → exit 0" '[ "$rc" -eq 0 ]'
check "nokaart → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'

# ----------------------------------------------------------------------------
echo "Task 18: --check-headers falls back to first-link match when doc's Kaart has no matching register row"
# Edge case: a future register-merge drops the row that owned the doc's
# Kaart, but the doc itself still has a header pointing at it. The script
# must NOT crash and NOT spuriously match a different row's Kaart — fall
# back to the legacy first-link picker so the operator sees a real Uitkomst
# mismatch (warning line) instead of a silent skip.
orphan="$TMP/orphan"; mkdir -p "$orphan"
{
  echo '# reg'
  echo
  echo '| d | v | **First.** | [`a-decision.md`](./a-decision.md) | `aaaa1111` |'
} > "$orphan/decisions.md"
cat > "$orphan/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `ffff9999`
**Uitkomst:** **First.** (legacy fallback — doc's Kaart is ffff9999, register has only aaaa1111)
EOF
out=$(DECISIONS_DIR="$orphan" bash "$SUT" --check-headers 2>&1); rc=$?
check "orphan → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "orphan → fallback found a row (no Kaart-row match, so it warns on Uitkomst mismatch)" 'echo "$out" | grep -qE "WARNING.*Uitkomst"'

# ----------------------------------------------------------------------------
echo "Task 19: --check-headers Kaart 8-char prefix is taken from a full-hex id (no truncation marker)"
# Some docs carry a 32-char hex id in their Kaart field with no `…` marker
# (the `1fafd87c19e54ef1aa48936e8759ce06` style in the real tree). The
# Kaart-prefix extractor must take the first 8 hex chars regardless of
# whether the rest of the id is `…` or more hex. Regression: if the
# extractor hard-codes "expect `…` after 8 chars", full-hex docs break.
fullhexdir="$TMP/fullhex"; mkdir -p "$fullhexdir"
{
  echo '# reg'
  echo
  echo '| d | v | **Fullhex match.** | [`a-decision.md`](./a-decision.md) | `abcdef0123456789abcdef0123456789` |'
} > "$fullhexdir/decisions.md"
cat > "$fullhexdir/a-decision.md" <<'EOF'
# Title
**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `abcdef0123456789abcdef0123456789`
**Uitkomst:** **Fullhex match.**
EOF
out=$(DECISIONS_DIR="$fullhexdir" bash "$SUT" --check-headers 2>&1); rc=$?
check "fullhex → exit 0" '[ "$rc" -eq 0 ]'
check "fullhex → exact clean-state OK line" 'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
