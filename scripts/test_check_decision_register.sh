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
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
