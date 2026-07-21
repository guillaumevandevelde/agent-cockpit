#!/usr/bin/env bash
# Test harness for scripts/baseline-bash-tests.sh + scripts/compare-bash-tests.sh.
#
# Mirrors scripts/test_pytest_baseline.sh structure (PASS/FAIL counters,
# ok/bad/check helpers, `Total: $PASS passed, $FAIL failed` summary line,
# `[ "$FAIL" -eq 0 ]` final exit gate). Exercises every code path worth
# verifying without paying for a real detached-worktree git fetch on the
# box:
#
#   1. arg parsing — --help on both scripts; --bad-arg rejected.
#   2. error paths — missing baseline → exit 2; unknown arg → exit 2.
#   3. capture pipeline — synthetic FAIL lines from fake harnesses flow
#      through the `grep -E '^  FAIL: ' | sed -E 's/^  FAIL: //'` filter
#      into the tab-separated `<harness-name>\tFAIL: <desc>` shape.
#   4. attribution math — comm -12/-23/-13 correctly buckets failures as
#      pre-existing / NEW / FIXED against a synthetic baseline.
#   5. idempotency — a fresh baseline file is reused on the next call
#      instead of being regenerated.
#   6. --print mode — leaves the cache untouched, reports the current state.
#   7. compare end-to-end — fake worktree + fake harnesses produce a
#      correct attribution header + NEW exit code.
#   8. --pre-existing-only — suppresses NEW/FIXED listing.
#   9. crashed-harness — zero FAIL lines + bash parse error → sentinel.
#  10. card-required coverage — `scripts/test_check_decision_register.sh`
#      Task 12 failure classified as pre-existing (literal FAIL: string
#      from origin/master today).
#
# Bash invocation itself runs against `BASH_TEST_FAKE_WORKTREE=1` +
# `BASH_TEST_CWD=<tmpdir>` overrides, mirroring `PYTEST_FAKE_WORKTREE`
# in test_pytest_baseline.sh. The fake-worktree mode is reached via a
# small synthetic `scripts/` dir the harness builds into a tmpdir — the
# scripts-under-test are fakes too (no real `bash scripts/test_x.sh`
# runs against the host's real `scripts/` dir; only Task 10 does, and
# only against the comparator with a pre-seeded baseline).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help on both scripts"
out1=$(bash "$SCRIPT_DIR/baseline-bash-tests.sh" --help 2>&1 || true)
check "baseline-bash-tests.sh --help mentions Usage" \
    'echo "$out1" | grep -qE "Usage:"'
check "baseline-bash-tests.sh --help mentions --regen" \
    'echo "$out1" | grep -qE "\-\-regen"'
check "baseline-bash-tests.sh --help mentions --print" \
    'echo "$out1" | grep -qE "\-\-print"'
check "baseline-bash-tests.sh --help mentions --max-age-hours" \
    'echo "$out1" | grep -qE "max-age-hours"'

out2=$(bash "$SCRIPT_DIR/compare-bash-tests.sh" --help 2>&1 || true)
check "compare-bash-tests.sh --help mentions Usage" \
    'echo "$out2" | grep -qE "Usage:"'
check "compare-bash-tests.sh --help mentions --pre-existing-only" \
    'echo "$out2" | grep -qE "\-\-pre-existing-only"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: error paths — unknown arg + missing baseline"
check "baseline-bash-tests.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/baseline-bash-tests.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'
check "compare-bash-tests.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/compare-bash-tests.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'

# Force the comparator into the "no baseline" branch by pointing it at a
# nonexistent path. Same shape as test_pytest_baseline.sh:71-74.
check "compare-bash-tests.sh exits 2 with no baseline cached" \
    '[ "$( ( BASH_TEST_BASELINE_PATH=/nonexistent/bash_baseline.txt bash "$SCRIPT_DIR/compare-bash-tests.sh" 2>&1 || true ) | grep -c "no baseline" )" -ge 1 ]'
check "missing baseline → exit code 2" \
    'BASH_TEST_BASELINE_PATH=/nonexistent/bash_baseline.txt bash "$SCRIPT_DIR/compare-bash-tests.sh" >/dev/null 2>&1; [ "$?" = "2" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 3: capture pipeline — synthetic FAIL lines survive the grep+sed filter"
TMPDIR=$(mktemp -d)

# Build a fake `scripts/` dir with two fake harnesses. Each prints known
# FAIL lines + a recognizable summary line.
fake_scripts="$TMPDIR/fake_scripts"
mkdir -p "$fake_scripts"
cat > "$fake_scripts/test_a.sh" <<'EOF'
#!/usr/bin/env bash
echo "  ok: a-1"
echo "  FAIL: alpha broken"
echo "  FAIL: alpha also broken"
echo ""
echo "Total: 1 passed, 2 failed"
exit 1
EOF
chmod +x "$fake_scripts/test_a.sh"
cat > "$fake_scripts/test_b.sh" <<'EOF'
#!/usr/bin/env bash
echo "  ok: b-1"
echo "  FAIL: beta broken"
echo ""
echo "Total: 1 passed, 1 failed"
exit 1
EOF
chmod +x "$fake_scripts/test_b.sh"

# Build the expected tab-separated baseline (sorted unique) by replaying the
# same pipeline the script uses against the same synthetic output.
fake_a_out="  ok: a-1
  FAIL: alpha broken
  FAIL: alpha also broken

Total: 1 passed, 2 failed"
fake_b_out="  ok: b-1
  FAIL: beta broken

Total: 1 passed, 1 failed"

expected="$TMPDIR/expected.txt"
{
    # harness test_a.sh
    printf '%s\n' "$fake_a_out" | grep -E '^  FAIL: ' | sed -E 's/^  FAIL: //' \
        | awk '{ printf "test_a.sh\tFAIL: %s\n", $0 }'
    # harness test_b.sh
    printf '%s\n' "$fake_b_out" | grep -E '^  FAIL: ' | sed -E 's/^  FAIL: //' \
        | awk '{ printf "test_b.sh\tFAIL: %s\n", $0 }'
} | sort -u > "$expected"

actual="$TMPDIR/actual.txt"
: > "$actual.tmp"
for h in "$fake_scripts"/test_*.sh; do
    name="$(basename "$h")"
    out="$(cd "$fake_scripts" && bash "$h" 2>&1)" || true
    fails="$(printf '%s\n' "$out" | grep -E '^  FAIL: ' | sed -E 's/^  FAIL: //')"
    while IFS= read -r line; do
        [ -n "$line" ] && printf '%s\tFAIL: %s\n' "$name" "$line" >> "$actual.tmp"
    done <<< "$fails"
done
sort -u "$actual.tmp" > "$actual"
rm -f "$actual.tmp"

check "pipeline produces 3 unique baseline lines" \
    '[ "$(wc -l < "$actual")" = "3" ]'
check "pipeline preserves test_a.sh FAIL: alpha broken" \
    'grep -q "^test_a\.sh	FAIL: alpha broken$" "$actual"'
check "pipeline preserves test_a.sh FAIL: alpha also broken" \
    'grep -q "^test_a\.sh	FAIL: alpha also broken$" "$actual"'
check "pipeline preserves test_b.sh FAIL: beta broken" \
    'grep -q "^test_b\.sh	FAIL: beta broken$" "$actual"'
check "pipeline drops ok lines" \
    '! grep -q "a-1" "$actual"'
check "pipeline drops summary lines" \
    '! grep -q "Total:" "$actual"'
diff "$expected" "$actual" >/dev/null && ok "pipeline output matches expected (set-equal)" \
    || bad "pipeline output differs from expected"

# ----------------------------------------------------------------------------
echo
echo "Task 4: attribution math — comm-based attribution against a synthetic baseline"
baseline_file="$TMPDIR/baseline.txt"
current_file="$TMPDIR/current.txt"
cat > "$baseline_file" <<'EOF'
test_a.sh	FAIL: alpha broken
test_b.sh	FAIL: beta broken
EOF
cat > "$current_file" <<'EOF'
test_a.sh	FAIL: alpha broken
test_c.sh	FAIL: gamma new
EOF

pre_count=$(comm -12 "$baseline_file" "$current_file" | wc -l | tr -d ' ')
new_list=$(comm -23 "$current_file" "$baseline_file" || true)
fixed_list=$(comm -13 "$current_file" "$baseline_file" || true)
new_count=$(printf '%s\n' "$new_list" | grep -c . || true)
fixed_count=$(printf '%s\n' "$fixed_list" | grep -c . || true)

check "pre_count == 1 (alpha broken in both)" '[ "$pre_count" = "1" ]'
check "new_count == 1 (gamma new is new)" '[ "$new_count" = "1" ]'
check "fixed_count == 1 (beta broken is gone)" '[ "$fixed_count" = "1" ]'
check "new_list includes gamma new" 'printf "%s" "$new_list" | grep -q "gamma new"'
check "fixed_list includes beta broken" 'printf "%s" "$fixed_list" | grep -q "beta broken"'

# Empty current: every baseline entry is "fixed", new_count = 0.
: > "$current_file"
new_count=$(comm -23 "$current_file" "$baseline_file" | wc -l | tr -d ' ')
pre_count=$(comm -12 "$baseline_file" "$current_file" | wc -l | tr -d ' ')
check "empty current → new_count == 0" '[ "$new_count" = "0" ]'
check "empty current → pre_count == 0" '[ "$pre_count" = "0" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 5: idempotency — a fresh baseline file is reused, not regenerated"
state_dir="$TMPDIR/state"
mkdir -p "$state_dir"
echo "test_x.sh	FAIL: example failure" > "$state_dir/bash-test-baseline.txt"

# Restructure the fake scripts into the layout the script-under-test
# expects: `$BASH_TEST_CWD/scripts/test_*.sh`. Tasks 3 used the flat layout
# to verify the filter pipeline; here we want the full script to discover
# them, so the `scripts/` subdir matters.
fake_scripts_layout="$TMPDIR/fake_scripts_layout"
mkdir -p "$fake_scripts_layout/scripts"
cp "$fake_scripts/test_a.sh" "$fake_scripts_layout/scripts/"
cp "$fake_scripts/test_b.sh" "$fake_scripts_layout/scripts/"

# Make BASH_TEST_CWD a no-op dir; BASH_TEST_FAKE_WORKTREE=1 skips the detached
# git worktree dance so we don't need a real origin/master fetch.
out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$TMPDIR" \
      bash "$SCRIPT_DIR/baseline-bash-tests.sh" 2>&1 || true)
check "second run reuses cache (prints 'Using cached baseline')" \
    'echo "$out" | grep -qE "Using cached baseline"'
check "second run does NOT print 'Captured baseline'" \
    '! echo "$out" | grep -qE "^Captured baseline"'

# --regen flips the flag and forces the script through the capture path.
out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts_layout" \
      bash "$SCRIPT_DIR/baseline-bash-tests.sh" --regen 2>&1 || true)
# In fake mode + the fake_scripts_layout dir, the script will discover both
# test_a.sh and test_b.sh, capture their FAIL lines, and write a 3-line
# baseline.
check "--regen flows through capture (file rewritten with 3 lines)" \
    '[ "$(wc -l < "$state_dir/bash-test-baseline.txt")" = "3" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 6: --print leaves the cache untouched and reports the current state"
echo "test_x.sh	FAIL: example" > "$state_dir/bash-test-baseline.txt"
before=$(cat "$state_dir/bash-test-baseline.txt")
out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      bash "$SCRIPT_DIR/baseline-bash-tests.sh" --print 2>&1 || true)
after=$(cat "$state_dir/bash-test-baseline.txt")
check "--print reports the cached file path" \
    'echo "$out" | grep -q "bash-test-baseline.txt"'
check "--print reports the count" \
    'echo "$out" | grep -qE "[0-9]+ pre-existing"'
check "--print does not modify the cache" \
    '[ "$before" = "$after" ]'

# --print with no cache: prints the "no baseline yet" hint and does not create
# one.
empty_state="$TMPDIR/empty_state"
mkdir -p "$empty_state"
out=$(BASH_TEST_BASELINE_PATH="$empty_state/bash-test-baseline.txt" \
      bash "$SCRIPT_DIR/baseline-bash-tests.sh" --print 2>&1 || true)
check "--print on missing cache → 'no baseline yet'" \
    'echo "$out" | grep -qE "no baseline yet"'
check "--print on missing cache does not create it" \
    '[ ! -e "$empty_state/bash-test-baseline.txt" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 7: compare-bash-tests.sh honors a fake worktree + faux baseline"
# Build a fake harness whose FAIL line matches a pre-seeded baseline line,
# plus a second whose FAIL line is new.
fake_scripts2="$TMPDIR/fake_scripts2"
mkdir -p "$fake_scripts2/scripts"
cat > "$fake_scripts2/scripts/test_preex.sh" <<'EOF'
#!/usr/bin/env bash
echo "  ok: preexist-1"
echo "  FAIL: preexist failure"
echo ""
echo "Total: 1 passed, 1 failed"
exit 1
EOF
chmod +x "$fake_scripts2/scripts/test_preex.sh"
cat > "$fake_scripts2/scripts/test_new.sh" <<'EOF'
#!/usr/bin/env bash
echo "  FAIL: brand new failure"
echo ""
echo "Total: 0 passed, 1 failed"
exit 1
EOF
chmod +x "$fake_scripts2/scripts/test_new.sh"

cat > "$state_dir/bash-test-baseline.txt" <<'EOF'
test_preex.sh	FAIL: preexist failure
EOF

out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts2" \
      bash "$SCRIPT_DIR/compare-bash-tests.sh" 2>&1 || true)
check "compare prints attribution header" \
    'echo "$out" | grep -qE "bash-test failure attribution"'
check "compare flags pre-existing failure" \
    'echo "$out" | grep -qE "pre-existing.*not your fault"'
check "compare flags the new failure with harness-name prefix" \
    'echo "$out" | grep -qE "test_new\.sh"'
check "compare flags the new failure description" \
    'echo "$out" | grep -qE "brand new failure"'

# Re-run the same call and verify the comparator exits non-zero when there's
# at least one new failure.
ec=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
     BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts2" \
     bash "$SCRIPT_DIR/compare-bash-tests.sh" >/dev/null 2>&1 || echo "$?")
check "compare exits 1 when new failures exist" \
    'echo "$ec" | grep -qE "^1$"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: --pre-existing-only suppresses the new/fixed listing"
out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts2" \
      bash "$SCRIPT_DIR/compare-bash-tests.sh" --pre-existing-only 2>&1 || true)
check "--pre-existing-only prints the count" \
    'echo "$out" | grep -qE "pre-existing failures.*1"'
check "--pre-existing-only does NOT print the attribution header" \
    '! echo "$out" | grep -qE "bash-test failure attribution"'
check "--pre-existing-only does NOT print NEW section" \
    '! echo "$out" | grep -qE "NEW.*needs fix"'

# ----------------------------------------------------------------------------
echo
echo "Task 9: crashed-harness handling — bash parse error → sentinel"
fake_scripts3="$TMPDIR/fake_scripts3"
mkdir -p "$fake_scripts3/scripts"
# Deliberately broken — missing `fi` → bash parse error, no FAIL lines.
cat > "$fake_scripts3/scripts/test_crash.sh" <<'EOF'
#!/usr/bin/env bash
if true; then
    echo "broken"
EOF
chmod +x "$fake_scripts3/scripts/test_crash.sh"
cat > "$fake_scripts3/scripts/test_ok.sh" <<'EOF'
#!/usr/bin/env bash
echo "  ok: nothing failed"
echo "Total: 1 passed, 0 failed"
exit 0
EOF
chmod +x "$fake_scripts3/scripts/test_ok.sh"

cat > "$state_dir/bash-test-baseline.txt" <<'EOF2'
EOF2
# empty baseline — every current failure is NEW

# We capture the comparator's stderr/stdout output and look for the sentinel.
out=$(BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" \
      BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts3" \
      bash "$SCRIPT_DIR/compare-bash-tests.sh" 2>&1 || true)
check "crashed harness flags as NEW (comparator exit nonzero)" \
    '[ "$( BASH_TEST_BASELINE_PATH="$state_dir/bash-test-baseline.txt" BASH_TEST_FAKE_WORKTREE=1 BASH_TEST_CWD="$fake_scripts3" bash "$SCRIPT_DIR/compare-bash-tests.sh" >/dev/null 2>&1 || echo "$?" )" = "1" ]'
check "crashed harness sentinel contains harness name" \
    'echo "$out" | grep -qE "test_crash\.sh.*crashed"'
check "crashed harness sentinel mentions FAIL lines" \
    'echo "$out" | grep -qE "crashed without FAIL lines"'

# ----------------------------------------------------------------------------
echo
echo "Task 10: card-required coverage — test_check_decision_register.sh Task 12 classified as pre-existing"
# This is the gate the card explicitly asks for. We seed a synthetic
# baseline with the literal FAIL: string the real script produces on
# origin/master today (commit d267233 left this real). The comparator is
# then run against the real scripts/ dir — it will re-discover the
# failure and attribute it as pre-existing.
card_state="$TMPDIR/card_state"
mkdir -p "$card_state"
cat > "$card_state/bash-test-baseline.txt" <<'EOF'
test_check_decision_register.sh	FAIL: real tree --check-headers --strict → exit 0
EOF

# Run the comparator against the REAL scripts/ dir to confirm attribution.
# Two skips:
#   - `test_cockpit.sh` — spawns the real backend (sleep 30/60/300 inside
#     its own `is_running` + pid-helper tests); too slow for a regression
#     harness and unrelated to the card's check.
#   - `test_baseline_bash_tests.sh` — the harness under test; its own
#     in-progress assertions would surface as "NEW" and pollute the
#     attribution check we're trying to make here.
out=$(BASH_TEST_BASELINE_PATH="$card_state/bash-test-baseline.txt" \
      BASH_TEST_CWD="$REPO_ROOT" \
      BASH_TEST_SKIP='^(test_cockpit|test_baseline_bash_tests)\.sh$' \
      bash "$SCRIPT_DIR/compare-bash-tests.sh" 2>&1 || true)
check "real compare produces the attribution header" \
    'echo "$out" | grep -qE "bash-test failure attribution"'
check "real compare classifies test_check_decision_register.sh as pre-existing" \
    'echo "$out" | grep -qE "test_check_decision_register\.sh"'
check "real compare shows pre-existing count >= 1" \
    'echo "$out" | grep -qE "pre-existing.*not your fault.*: [1-9]"'
# We expect no NEW line for test_check_decision_register.sh — the only
# pre-existing failure on master is exactly the one we seeded.
check "real compare does NOT list test_check_decision_register.sh as NEW" \
    '! (echo "$out" | awk "/^NEW/,/^\$/" | grep -q "test_check_decision_register\.sh")'

# ----------------------------------------------------------------------------
mv "$TMPDIR" /tmp/_bash_test_baseline_artifacts >/dev/null 2>&1 || true
echo
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]