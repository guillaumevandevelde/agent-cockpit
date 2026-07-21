#!/usr/bin/env bash
# Test harness for scripts/check-test-harness-coverage.sh.
#
# Coverage:
#   1. arg parsing — --help works; unknown arg → exit 2.
#   2. clean case — every scripts/test_*.sh on disk appears in the
#      CLAUDE.md # Test block, and nothing else is listed → exit 0.
#   3. harness missing from CLAUDE.md → flagged with the right path.
#   4. phantom listed but not on disk → flagged.
#   5. both directions at once → both flags emitted.
#   6. --strict turns drift into a non-zero exit.
#   7. block boundary — a `# Test` mention outside the `# Test` heading
#      block does NOT count as a listing. Mirrors the card's note that
#      `test_cockpit.sh` lives in the supervisor section, not the
#      `# Test` block, and should be treated as missing from the index.
#   8. block boundary — a `## Test` sub-heading does not start the block
#      (must be `# Test`, not `## Test`).
#   9. CLAUDE.md path resolution — CLAUDE_MD env var overrides default.
#  10. error path — missing CLAUDE_MD → exit 2.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-test-harness-coverage.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage"        'echo "$out" | grep -qE "Usage:"'
check "--help mentions --strict"     'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions CLAUDE.md"    'echo "$out" | grep -qF "CLAUDE.md"'

out=$(bash "$SUT" --bogus 2>&1); rc=$?
check "unknown arg → exit 2"         '[ "$rc" -eq 2 ]'
check "unknown arg → ERROR"          'echo "$out" | grep -qE "ERROR:.*unknown argument"'

# ----------------------------------------------------------------------------
# Helper: build a synthetic SCRIPTS_DIR with the given test_*.sh files
# (touch each), and a synthetic CLAUDE.md with a `# Test` block listing
# the given `scripts/test_<name>.sh` lines. Outputs the two paths.
make_fixture() {
  local scripts_dir="$TMP/scripts-$1"; shift
  local claude_md="$TMP/CLAUDE-$1.md"; shift
  mkdir -p "$scripts_dir"
  touch "$scripts_dir"/test_*.sh 2>/dev/null || true
  # The above `touch` doesn't take lists — caller creates files explicitly
  # via the next loop.
  rm -f "$scripts_dir"/test_*.sh
  for f in "$@"; do
    case "$f" in
      scripts/test_*) touch "$scripts_dir/${f#scripts/}" ;;
      claude:*)       echo "${f#claude:}" >> "$claude_md" ;;
    esac
  done
  printf '%s\n' "$scripts_dir" "$claude_md"
}

# Helper: write a CLAUDE.md fixture with a `# Test` block containing the
# supplied bash-command lines. Args after $1 (the path) are the lines.
write_claude() {
  local claude_md="$1"; shift
  cat > "$claude_md" <<'HEADER'
# Some leading heading

A paragraph that mentions scripts/test_foo.sh and test_bar.sh in prose,
but does NOT live inside the # Test block. Should be ignored.

# Test
HEADER
  for line in "$@"; do
    printf '%s\n' "$line" >> "$claude_md"
  done
  cat >> "$claude_md" <<'FOOTER'

# Next heading

Prose: see scripts/test_baz.sh for details.
FOOTER
}

# Helper: create N empty test_*.sh files inside a scripts dir.
seed_scripts() {
  local scripts_dir="$1"; shift
  mkdir -p "$scripts_dir"
  for name in "$@"; do
    : > "$scripts_dir/$name"
  done
}

# ----------------------------------------------------------------------------
echo "Task 2: clean case — every script listed, no phantoms"
clean_scripts="$TMP/clean-scripts"
clean_claude="$TMP/clean-claude.md"
seed_scripts "$clean_scripts" test_a.sh test_b.sh
write_claude "$clean_claude" \
  "bash scripts/test_a.sh     # A" \
  "bash scripts/test_b.sh     # B"
out=$(SCRIPTS_DIR="$clean_scripts" CLAUDE_MD="$clean_claude" bash "$SUT" 2>&1); rc=$?
check "clean → exit 0"               '[ "$rc" -eq 0 ]'
check "clean → OK: line"             'echo "$out" | grep -qE "^OK:"'
check "clean → no WARNING"           '! echo "$out" | grep -qE "WARNING"'

# ----------------------------------------------------------------------------
echo "Task 3: harness on disk but not in CLAUDE.md # Test → flagged"
miss_scripts="$TMP/miss-scripts"
miss_claude="$TMP/miss-claude.md"
seed_scripts "$miss_scripts" test_listed.sh test_unlisted.sh
write_claude "$miss_claude" "bash scripts/test_listed.sh"
out=$(SCRIPTS_DIR="$miss_scripts" CLAUDE_MD="$miss_claude" bash "$SUT" 2>&1); rc=$?
check "missing-from-claude → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "missing-from-claude → WARNING"            'echo "$out" | grep -qE "WARNING"'
check "missing-from-claude → names test_unlisted.sh" 'echo "$out" | grep -qF "test_unlisted.sh"'
check "missing-from-claude → does NOT name test_listed.sh" '! echo "$out" | grep -qF "test_listed.sh"'

# ----------------------------------------------------------------------------
echo "Task 4: phantom listed but not on disk → flagged"
phantom_scripts="$TMP/phantom-scripts"
phantom_claude="$TMP/phantom-claude.md"
seed_scripts "$phantom_scripts" test_real.sh
write_claude "$phantom_claude" \
  "bash scripts/test_real.sh" \
  "bash scripts/test_ghost.sh"
out=$(SCRIPTS_DIR="$phantom_scripts" CLAUDE_MD="$phantom_claude" bash "$SUT" 2>&1); rc=$?
check "phantom → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
check "phantom → WARNING"            'echo "$out" | grep -qE "WARNING"'
check "phantom → names test_ghost.sh" 'echo "$out" | grep -qF "test_ghost.sh"'

# ----------------------------------------------------------------------------
echo "Task 5: both directions at once → both flagged"
both_scripts="$TMP/both-scripts"
both_claude="$TMP/both-claude.md"
# test_unlisted.sh exists on disk but is not listed.
# test_phantom.sh is listed but does not exist on disk.
# test_in_both.sh exists in both places (does not contribute to drift).
seed_scripts "$both_scripts" test_in_both.sh test_unlisted.sh
write_claude "$both_claude" \
  "bash scripts/test_in_both.sh" \
  "bash scripts/test_phantom.sh"
out=$(SCRIPTS_DIR="$both_scripts" CLAUDE_MD="$both_claude" bash "$SUT" 2>&1); rc=$?
check "both → exit 0 (advisory)"     '[ "$rc" -eq 0 ]'
check "both → WARNING missing"       'echo "$out" | grep -qE "not listed in CLAUDE.md"'
check "both → WARNING missing names test_unlisted.sh" \
  'echo "$out" | grep -qF "test_unlisted.sh"'
check "both → WARNING phantom"       'echo "$out" | grep -qE "do not exist on disk"'
check "both → WARNING phantom names test_phantom.sh" \
  'echo "$out" | grep -qF "test_phantom.sh"'

# ----------------------------------------------------------------------------
echo "Task 6: --strict turns drift into a non-zero exit"
out=$(SCRIPTS_DIR="$both_scripts" CLAUDE_MD="$both_claude" \
      bash "$SUT" --strict 2>&1); rc=$?
check "both + --strict → exit 1"     '[ "$rc" -eq 1 ]'
check "both + --strict → ERROR"      'echo "$out" | grep -qE "ERROR:"'

# Confirm the no-drift + --strict combination still exits 0.
out=$(SCRIPTS_DIR="$clean_scripts" CLAUDE_MD="$clean_claude" \
      bash "$SUT" --strict 2>&1); rc=$?
check "clean + --strict → exit 0"    '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 7: prose mention outside # Test does NOT count as a listing"
# Mirrors the card's carve-out: CLAUDE.md mentions `test_cockpit.sh` in
# the supervisor section, but the `# Test` block doesn't list it, so the
# script must still flag it as missing from the index.
cardlike_scripts="$TMP/cardlike-scripts"
cardlike_claude="$TMP/cardlike-claude.md"
seed_scripts "$cardlike_scripts" test_cockpit.sh
cat > "$cardlike_claude" <<'EOF'
# Self-healing dev stack (detached supervisor: auto-restart on crash, logs to logs/, survives terminal close)
./scripts/cockpit.sh start       # Start backend+frontend supervised (auto-installs missing/stale deps)
bash scripts/test_cockpit.sh     # Test the supervisor (bash harness)

# Build
./scripts/build.sh               # Production frontend build → frontend/dist

# Test
bash scripts/test_other.sh

# Some next heading
EOF
out=$(SCRIPTS_DIR="$cardlike_scripts" CLAUDE_MD="$cardlike_claude" bash "$SUT" 2>&1); rc=$?
check "prose-only mention → flagged as missing" \
  'echo "$out" | grep -qF "test_cockpit.sh"'
check "prose-only mention → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 8: ## Test sub-heading does NOT start the block"
sub_scripts="$TMP/sub-scripts"
sub_claude="$TMP/sub-claude.md"
seed_scripts "$sub_scripts" test_a.sh
cat > "$sub_claude" <<'EOF'
# Top heading

## Test detail (sub-heading — not the index)
prose only, no listings

# Test
bash scripts/test_a.sh
EOF
out=$(SCRIPTS_DIR="$sub_scripts" CLAUDE_MD="$sub_claude" bash "$SUT" 2>&1); rc=$?
check "## Test → ignored as block start"  '[ "$rc" -eq 0 ]'
check "## Test → clean (test_a.sh found)" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 9: CLAUDE_MD env var overrides default"
# Default CLAUDE.md at the repo root is the drift state; pointing the env
# at our clean fixture should make the same shell script exit 0 even
# without changing the disk state of CLAUDE.md.
out=$(CLAUDE_MD="$clean_claude" SCRIPTS_DIR="$clean_scripts" \
      bash "$SUT" 2>&1); rc=$?
check "CLAUDE_MD env override → exit 0" '[ "$rc" -eq 0 ]'
check "CLAUDE_MD env override → OK"     'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 10: missing CLAUDE_MD → exit 2"
out=$(CLAUDE_MD="$TMP/does-not-exist.md" bash "$SUT" 2>&1); rc=$?
check "missing CLAUDE_MD → exit 2"      '[ "$rc" -eq 2 ]'
check "missing CLAUDE_MD → ERROR"       'echo "$out" | grep -qE "ERROR:.*CLAUDE.md"'

out=$(SCRIPTS_DIR="$TMP/does-not-exist" CLAUDE_MD="$clean_claude" \
      bash "$SUT" 2>&1); rc=$?
check "missing SCRIPTS_DIR → exit 2"    '[ "$rc" -eq 2 ]'
check "missing SCRIPTS_DIR → ERROR"     'echo "$out" | grep -qE "ERROR:.*scripts directory"'

# ----------------------------------------------------------------------------
echo "Task 11: real repo CLAUDE.md is reported as drift (advisory)"
# Sanity check that the actual repo state is what the card described —
# unless we've already updated CLAUDE.md, the harness should warn. We
# run WITHOUT --strict so the test stays green even after the CLAUDE.md
# update lands (this test will simply start reporting clean).
out=$(bash "$SUT" 2>&1); rc=$?
# The check is advisory; we just verify it runs and prints OK or WARNING.
check "real repo CLAUDE.md → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "real repo CLAUDE.md → emits OK or WARNING" \
  'echo "$out" | grep -qE "^(OK|WARNING)"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]