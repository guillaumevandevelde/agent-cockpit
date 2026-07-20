#!/usr/bin/env bash
# Test harness for scripts/check-doc-links.sh.
#
# Exercises relative Markdown-link validation against synthetic fixture dirs:
#
#   1. arg parsing — `--help` works.
#   2. clean case — same-dir, parent, bare, anchored, and image targets exist.
#   3. non-local links — document anchors and external URLs are ignored.
#   4. drift case — a missing target is reported without failing (advisory).
#   5. anchors — the fragment is stripped before checking the target.
#   6. --strict — the same drift exits 1.
#   7. error path — a missing docs directory exits 2.
#   8. real tree — docs/cockpit has no broken relative links.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-doc-links.sh"

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
echo "Task 2/3: clean relative links and ignored non-local links"
clean="$TMP/clean/docs"; mkdir -p "$clean/assets"
printf '# Same directory\n' > "$clean/target.md"
printf '# Parent directory\n' > "$TMP/clean/shared.md"
printf 'image' > "$clean/assets/diagram.png"
cat > "$clean/index.md" <<'EOF'
[explicit same-dir](./target.md)
[bare same-dir](target.md)
[parent with anchor](../shared.md#details)
![relative image](./assets/diagram.png)
[document anchor](#local-heading)
[external URL](https://example.com/missing)
[mail address](mailto:test@example.com)
EOF
out=$(DOCS_DIR="$clean" bash "$SUT" 2>&1); rc=$?
check "clean → exit 0" '[ "$rc" -eq 0 ]'
check "clean → prints OK" 'echo "$out" | grep -qE "^OK:"'
check "clean → no warnings" '! echo "$out" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 4/5: missing anchored target is advisory and reported"
drift="$TMP/drift"; mkdir -p "$drift"
cat > "$drift/source.md" <<'EOF'
[missing source of truth](./missing.md#canonical-section)
EOF
out=$(DOCS_DIR="$drift" bash "$SUT" 2>&1); rc=$?
check "drift → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "drift → prints WARNING" 'echo "$out" | grep -qE "WARNING:"'
check "drift → names the source doc" 'echo "$out" | grep -qF "source.md"'
check "drift → reports the original anchored link" 'echo "$out" | grep -qF "./missing.md#canonical-section"'
check "drift → reports exactly 1 broken link" 'echo "$out" | grep -qE "WARNING: 1 broken relative Markdown link"'

# Prove the anchor is removed for the filesystem check: creating the path without
# a literal #canonical-section makes the same fixture clean.
printf '# Now present\n' > "$drift/missing.md"
out=$(DOCS_DIR="$drift" bash "$SUT" 2>&1); rc=$?
check "existing target with anchor → exit 0" '[ "$rc" -eq 0 ]'
check "existing target with anchor → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 6: --strict turns drift into a failure"
strict="$TMP/strict"; mkdir -p "$strict"
printf '[missing](../absent.md)\n' > "$strict/source.md"
out=$(DOCS_DIR="$strict" bash "$SUT" --strict 2>&1); rc=$?
check "drift + --strict → exit 1" '[ "$rc" -eq 1 ]'
check "drift + --strict → still names the target" 'echo "$out" | grep -qF "../absent.md"'

# ----------------------------------------------------------------------------
echo "Task 7: error path — missing docs directory"
out=$(DOCS_DIR="$TMP/does-not-exist" bash "$SUT" 2>&1); rc=$?
check "missing docs directory → exit 2" '[ "$rc" -eq 2 ]'
check "missing docs directory → ERROR" 'echo "$out" | grep -qE "ERROR:.*docs directory"'

# ----------------------------------------------------------------------------
echo "Task 8: the repo's docs/cockpit tree is link-clean"
out=$(bash "$SUT" --strict 2>&1); rc=$?
check "real docs/cockpit → exit 0 under --strict" '[ "$rc" -eq 0 ]'
check "real docs/cockpit → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
