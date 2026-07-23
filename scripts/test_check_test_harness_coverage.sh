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
#   7. carve-out list — a script listed in the SUT's CARVE_OUTS list is
#      silently excluded from MISSING_FROM_CLAUDE drift, even when it has
#      no prose mention in CLAUDE.md at all. The card's hard case:
#      test_cockpit.sh lives in the supervisor section (not `# Test`),
#      and is on the carve-out list by explicit human decision.
#   8. block boundary — a `## Test` sub-heading does not start the block
#      (must be `# Test`, not `## Test`).
#   9. CLAUDE.md path resolution — CLAUDE_MD env var overrides default.
#  10. error path — missing CLAUDE_MD → exit 2.
#  11. carve-out env override — CARVE_OUTS env var lets the test harness
#      exercise the carve-out path against arbitrary fixtures without
#      editing the SUT.
#  12. carve-out does NOT mask phantoms — a carved-out name that *is*
#      listed in `# Test` but doesn't exist on disk is still a phantom
#      drift (the carve-out only suppresses direction A).
#  13. real repo is CLEAN — the actual repo CLAUDE.md is fully covered
#      after the carve-out for test_cockpit.sh (and test_measure_token_saver.sh)
#      so the guard reports OK; --strict exits 0.
#  14. CARVE_OUTS='' disables the carve-out entirely — operators who want
#      strict-by-default behavior can opt out via the env var.
#  15. family-level glob — a literal `scripts/test_*.sh` in the # Test
#      block covers every on-disk harness of that family (self-improve
#      card 8c7cfc14; regex previously only matched specific names).
#  16. glob + missing harness — the family glob still covers harnesses
#      that are NOT explicitly listed (no false-positive drift).
#  17. glob + phantom specific — a specific name listed alongside the
#      glob is still checked against the phantom direction (a typo'd
#      or stale name fires even when the family is covered).

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
echo "Task 7: CARVE_OUTS list silently absorbs legitimate outside-block harnesses"
# Default CARVE_OUTS contains `test_cockpit.sh` and `test_measure_token_saver.sh`
# (supervisor + token-saver-meet feature harness). When any of these files
# is on disk and not listed in `# Test`, the guard must NOT flag it as
# drift. We exercise this with the env override so the test doesn't
# depend on the SUT's literal default list (covered separately in Task 11).
# Fixture: ONLY `test_xxx_carved.sh` on disk and the carve-out list contains
# only that name. No `# Test` listing — we want to isolate direction A.
cardlike_scripts="$TMP/cardlike-scripts"
cardlike_claude="$TMP/cardlike-claude.md"
seed_scripts "$cardlike_scripts" test_xxx_carved.sh
cat > "$cardlike_claude" <<'EOF'
# Self-healing dev stack (detached supervisor: auto-restart on crash, logs to logs/, survives terminal close)
./scripts/cockpit.sh start       # Start backend+frontend supervised (auto-installs missing/stale deps)

# Build
./scripts/build.sh               # Production frontend build → frontend/dist

# Some next heading
EOF
# Override the carve-out list to the exact name we care about, then
# verify the script does NOT flag test_xxx_carved.sh as drift.
out=$(CARVE_OUTS="test_xxx_carved.sh" \
      SCRIPTS_DIR="$cardlike_scripts" CLAUDE_MD="$cardlike_claude" \
      bash "$SUT" 2>&1); rc=$?
check "carve-out absorbs missing-from-CLAUDE.md"  '[ "$rc" -eq 0 ]'
check "carve-out → no WARNING emitted"           '! echo "$out" | grep -qE "WARNING"'
check "carve-out → OK line emitted"              'echo "$out" | grep -qE "^OK:"'

# Confirm that WITHOUT the carve-out, the same fixture WOULD be flagged —
# proves the test is exercising the carve-out path, not a coincidental OK.
out=$(SCRIPTS_DIR="$cardlike_scripts" CLAUDE_MD="$cardlike_claude" \
      bash "$SUT" 2>&1); rc=$?
check "no carve-out → WARNING for uncarved name" 'echo "$out" | grep -qF "test_xxx_carved.sh"'

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
echo "Task 11: real repo CLAUDE.md is CLEAN (advisory + --strict both 0)"
# After the carve-out lands, the actual repo CLAUDE.md is fully covered:
# every scripts/test_*.sh on disk is either listed in `# Test` or on the
# CARVE_OUTS list. The guard must report OK, exit 0 (advisory), and exit 0
# under --strict — the latter is what makes the guard CI-usable.
out=$(bash "$SUT" 2>&1); rc=$?
check "real repo CLAUDE.md → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "real repo CLAUDE.md → emits OK (no drift)" \
  'echo "$out" | grep -qE "^OK:.*covered"'

out=$(bash "$SUT" --strict 2>&1); rc=$?
check "real repo CLAUDE.md + --strict → exit 0"  '[ "$rc" -eq 0 ]'
check "real repo CLAUDE.md + --strict → no ERROR" \
  '! echo "$out" | grep -qE "ERROR:"'

# ----------------------------------------------------------------------------
echo "Task 12: CARVE_OUTS env override (space-separated basenames)"
# The carve-out list is also exercisable via env var so tests can probe
# the filtering logic against synthetic fixtures. Use names that are NOT
# in the SUT's default carve-out list (test_cockpit.sh, test_measure_token_saver.sh)
# so the env override is the only thing that can silence them.
carve_scripts="$TMP/carve-scripts"
carve_claude="$TMP/carve-claude.md"
seed_scripts "$carve_scripts" test_zz_keep.sh test_zz_drop.sh
write_claude "$carve_claude" "bash scripts/test_zz_keep.sh"

out=$(CARVE_OUTS="test_zz_drop.sh" \
      SCRIPTS_DIR="$carve_scripts" CLAUDE_MD="$carve_claude" \
      bash "$SUT" 2>&1); rc=$?
check "CARVE_OUTS override → silenced test_zz_drop.sh"  '[ "$rc" -eq 0 ]'
check "CARVE_OUTS override → emits OK"                 'echo "$out" | grep -qE "^OK:"'

# Without the override, test_zz_drop.sh would flag — sanity check that
# the test fixture is actually testing what we think it's testing.
out=$(SCRIPTS_DIR="$carve_scripts" CLAUDE_MD="$carve_claude" \
      bash "$SUT" 2>&1); rc=$?
check "no CARVE_OUTS override → test_zz_drop.sh flagged" \
  'echo "$out" | grep -qF "test_zz_drop.sh"'

# ----------------------------------------------------------------------------
echo "Task 13: CARVE_OUTS does NOT mask phantom direction"
# A carved-out name that *is* listed in `# Test` but doesn't exist on
# disk is still a phantom drift — the carve-out only suppresses
# direction A (missing-from-CLAUDE.md). The phantom direction fires
# regardless, because CLAUDE.md is the source of truth for # Test.
phantom_carve_scripts="$TMP/phantom-carve-scripts"
phantom_carve_claude="$TMP/phantom-carve-claude.md"
seed_scripts "$phantom_carve_scripts" test_other.sh
write_claude "$phantom_carve_claude" \
  "bash scripts/test_carved_but_missing.sh" \
  "bash scripts/test_other.sh"
out=$(CARVE_OUTS="test_carved_but_missing.sh" \
      SCRIPTS_DIR="$phantom_carve_scripts" CLAUDE_MD="$phantom_carve_claude" \
      bash "$SUT" 2>&1); rc=$?
check "carved name in # Test but missing on disk → still flagged as phantom" \
  'echo "$out" | grep -qF "test_carved_but_missing.sh"'
check "carved phantom → WARNING emitted"  'echo "$out" | grep -qE "WARNING"'

# ----------------------------------------------------------------------------
echo "Task 14: CARVE_OUTS='' disables the carve-out entirely"
# Setting CARVE_OUTS to the empty string is an explicit opt-out of the
# carve-out mechanism — useful for operators who want the guard to be
# strict-by-default (every missing-from-CLAUDE.md is flagged, no
# exceptions). Use a name that IS in the default list (test_cockpit.sh)
# so we can prove the default is bypassed.
empty_carve_scripts="$TMP/empty-carve-scripts"
empty_carve_claude="$TMP/empty-carve-claude.md"
seed_scripts "$empty_carve_scripts" test_cockpit.sh
cat > "$empty_carve_claude" <<'EOF'
# Build
./scripts/build.sh
EOF
out=$(CARVE_OUTS="" \
      SCRIPTS_DIR="$empty_carve_scripts" CLAUDE_MD="$empty_carve_claude" \
      bash "$SUT" 2>&1); rc=$?
check "CARVE_OUTS='' → test_cockpit.sh flagged despite default carve-out" \
  'echo "$out" | grep -qF "test_cockpit.sh"'
check "CARVE_OUTS='' → exit 0 (advisory)"        '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo 'Task 15: family-level `scripts/test_*.sh` glob in # Test covers all on-disk'
# A literal `scripts/test_*.sh` reference in the # Test block is a
# family-level statement — every on-disk `scripts/test_*.sh` is implicitly
# covered. Self-improve card 8c7cfc14 documented the previous gap where
# the regex only matched specific names and the family statement fired
# 19 false-positive drift items.
glob_scripts="$TMP/glob-scripts"
glob_claude="$TMP/glob-claude.md"
seed_scripts "$glob_scripts" test_glob_a.sh test_glob_b.sh test_glob_c.sh
write_claude "$glob_claude" \
  "ls scripts/test_*.sh     # family-level reference — covers all on-disk"
out=$(SCRIPTS_DIR="$glob_scripts" CLAUDE_MD="$glob_claude" \
      bash "$SUT" 2>&1); rc=$?
check "glob → exit 0 (advisory)"           '[ "$rc" -eq 0 ]'
check "glob → no WARNING for any on-disk"  '! echo "$out" | grep -qE "WARNING"'
check "glob → OK line emitted"             'echo "$out" | grep -qE "^OK:.*covered"'
check "glob → reports family-level in OK"  'echo "$out" | grep -qE "family-level glob"'

# Glob fixture must work under --strict too (CI-usable).
out=$(SCRIPTS_DIR="$glob_scripts" CLAUDE_MD="$glob_claude" \
      bash "$SUT" --strict 2>&1); rc=$?
check "glob + --strict → exit 0"           '[ "$rc" -eq 0 ]'
check "glob + --strict → no ERROR"         '! echo "$out" | grep -qE "ERROR:"'

# ----------------------------------------------------------------------------
echo "Task 16: family glob + unlisted on-disk harness → no false-positive drift"
# The previous regex bug raised a false positive for every on-disk harness
# not explicitly listed; the glob must suppress that whole direction while
# the phantom direction stays unaffected (covered in Task 17).
glob_miss_scripts="$TMP/glob-miss-scripts"
glob_miss_claude="$TMP/glob-miss-claude.md"
seed_scripts "$glob_miss_scripts" test_glob_alpha.sh test_glob_beta.sh test_glob_gamma.sh
write_claude "$glob_miss_claude" \
  "ls scripts/test_*.sh     # family reference — alpha/beta/gamma are implicitly covered"
out=$(SCRIPTS_DIR="$glob_miss_scripts" CLAUDE_MD="$glob_miss_claude" \
      bash "$SUT" 2>&1); rc=$?
check "glob covers all on-disk → exit 0"    '[ "$rc" -eq 0 ]'
check "glob covers all on-disk → OK"       'echo "$out" | grep -qE "^OK:"'
check "glob → does not name test_glob_alpha.sh" \
  '! echo "$out" | grep -qF "test_glob_alpha.sh"'
check "glob → does not name test_glob_beta.sh" \
  '! echo "$out" | grep -qF "test_glob_beta.sh"'
check "glob → does not name test_glob_gamma.sh" \
  '! echo "$out" | grep -qF "test_glob_gamma.sh"'

# Sanity: drop the glob and confirm the same fixtures WOULD now flag —
# proves the test exercises the glob path, not a coincidental OK.
out=$(SCRIPTS_DIR="$glob_miss_scripts" CLAUDE_MD="$glob_miss_claude" \
      bash "$SUT" 2>&1 | tr -d '\n')
# Strip the glob line from a copy of the claude fixture and re-run.
sed 's|^ls scripts/test_\*\.sh.*$||' "$glob_miss_claude" > "$glob_miss_claude.noglob"
out=$(SCRIPTS_DIR="$glob_miss_scripts" CLAUDE_MD="$glob_miss_claude.noglob" \
      bash "$SUT" 2>&1); rc=$?
check "no-glob → exit 0 (advisory)"        '[ "$rc" -eq 0 ]'
check "no-glob → names test_glob_alpha.sh" \
  'echo "$out" | grep -qF "test_glob_alpha.sh"'

# ----------------------------------------------------------------------------
echo "Task 17: family glob + phantom specific name → phantom direction still fires"
# The glob covers the family for direction A (missing-from-CLAUDE.md), but
# a specific name listed alongside it must still be checked against
# direction B (phantom). A typo'd or stale name fires even when the
# family reference is otherwise valid.
glob_phantom_scripts="$TMP/glob-phantom-scripts"
glob_phantom_claude="$TMP/glob-phantom-claude.md"
seed_scripts "$glob_phantom_scripts" test_glob_real.sh
write_claude "$glob_phantom_claude" \
  "ls scripts/test_*.sh     # family reference" \
  "bash scripts/test_glob_ghost.sh     # typo'd / stale specific name"
out=$(SCRIPTS_DIR="$glob_phantom_scripts" CLAUDE_MD="$glob_phantom_claude" \
      bash "$SUT" 2>&1); rc=$?
check "glob + phantom → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "glob + phantom → WARNING for phantom" \
  'echo "$out" | grep -qF "test_glob_ghost.sh"'
check "glob + phantom → phantom direction label" \
  'echo "$out" | grep -qE "do not exist on disk"'
check "glob + phantom → no missing-from-CLAUDE drift for real harness" \
  '! echo "$out" | grep -qF "test_glob_real.sh"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]