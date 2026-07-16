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
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
