#!/usr/bin/env bash
# Test harness for scripts/pytest-baseline.sh + scripts/pytest-compare.sh.
#
# Exercises the parts worth verifying without paying for a real pytest
# run on the box:
#
#   1. arg parsing — `--help` works, `--bad-arg` rejected.
#   2. error paths — missing baseline → exit 2, missing venv → exit 1.
#   3. categorization — the diff logic correctly attributes failures to
#      "pre-existing", "new", or "fixed" against a synthetic baseline.
#   4. idempotency — a fresh baseline file is reused on the next call
#      instead of being regenerated.
#   5. parse filter — pytest's FAILED/ERROR lines survive the `sed` + `sort -u`
#      pipeline that's used to build the baseline.
#
# Pytest invocation itself runs against a FAKE pytest binary (a shell stub
# that prints canned output), enabled by setting PYTEST_CMD + PYTEST_CWD +
# PYTEST_FAKE_WORKTREE before invoking the scripts under test. CI runs the
# real pytest (GitHub Actions `quality.yml`); this harness covers everything
# that doesn't need the production venv.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help on both scripts"
out1=$(bash "$SCRIPT_DIR/pytest-baseline.sh" --help 2>&1 || true)
check "pytest-baseline.sh --help mentions Usage" \
    'echo "$out1" | grep -qE "Usage:"'
check "pytest-baseline.sh --help mentions --regen" \
    'echo "$out1" | grep -qE "\-\-regen"'

out2=$(bash "$SCRIPT_DIR/pytest-compare.sh" --help 2>&1 || true)
check "pytest-compare.sh --help mentions --pre-existing-only" \
    'echo "$out2" | grep -qE "\-\-pre-existing-only"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: error paths — unknown args + missing baseline + missing venv"
# Note on shell quoting: the inner expression is wrapped in a subshell+pipe,
# so we have to group with `(...)` to keep the `|| true` on the bash side.
# Without grouping, pipe precedence binds `||` to the grep side and the
# captured output is the bash error + grep's "0" — not what we want.
check "pytest-baseline.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/pytest-baseline.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'
check "pytest-compare.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/pytest-compare.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'

# Force the comparator into the "no baseline" path by pointing it at a
# nonexistent path. /dev/null is technically a device, not a regular file,
# so `[ -f /dev/null ]` returns false and the script would *also* exit with
# "no baseline" — use a real-missing path so we exercise the right branch.
check "pytest-compare.sh exits 2 with no baseline cached" \
    '[ "$( ( PYTEST_BASELINE_PATH=/nonexistent/baseline.txt bash "$SCRIPT_DIR/pytest-compare.sh" 2>&1 || true ) | grep -c "no baseline" )" -ge 1 ]'
check "missing baseline → exit code 2" \
    'PYTEST_BASELINE_PATH=/nonexistent/baseline.txt bash "$SCRIPT_DIR/pytest-compare.sh" >/dev/null 2>&1; [ "$?" = "2" ]'

# Missing venv: point PYTEST_CMD at something nonexistent and assert exit 1.
# Here the baseline path resolves to an existing file (touch'd below), so the
# script reaches the PYTEST_CMD check and exits with the expected code 1.
TMPDIR=$(mktemp -d)
fake_baseline="$TMPDIR/baseline_real.txt"
: > "$fake_baseline"
check "missing venv → exit code 1" \
    'PYTEST_CMD=/nonexistent/pytest PYTEST_BASELINE_PATH="$fake_baseline" bash "$SCRIPT_DIR/pytest-compare.sh" >/dev/null 2>&1; [ "$?" = "1" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 3: parse filter — pytest FAILED/ERROR lines survive the sed + sort -u pipeline"
cat > "$TMPDIR/fake_pytest_output.txt" <<'EOF'
============================= test session starts ==============================
platform linux -- Python 3.11.9, pytest-8.0.0, pluggy-1.0.0
rootdir: /tmp/fakework/backend
collected 3 items
tests/test_a.py::test_one FAILED
tests/test_a.py::test_two PASSED
tests/test_b.py::test_three ERROR
=========================== short test summary info ============================
FAILED tests/test_a.py::test_one - assert 1 == 2
FAILED tests/test_a.py::test_two - KeyError: 'foo'
ERROR tests/test_b.py::test_three - OSError: disk full
================= 1 failed, 1 error, 1 passed in 0.05s ==================
EOF
expected_file="$TMPDIR/expected.txt"
cat > "$expected_file" <<'EOF'
tests/test_a.py::test_one
tests/test_a.py::test_two
tests/test_b.py::test_three
EOF

# Run the same pipeline the script uses.
actual_file="$TMPDIR/actual.txt"
grep -E '^(FAILED|ERROR) ' "$TMPDIR/fake_pytest_output.txt" \
    | sed -E 's/^(FAILED|ERROR) +//; s/ +- .*$//' \
    | sort -u > "$actual_file"
check "pipeline produces 3 unique test names" \
    '[ "$(wc -l < "$actual_file")" = "3" ]'
check "pipeline preserves tests/test_a.py::test_one" \
    'grep -q "^tests/test_a\.py::test_one$" "$actual_file"'
check "pipeline preserves tests/test_b.py::test_three" \
    'grep -q "^tests/test_b\.py::test_three$" "$actual_file"'
check "pipeline drops the summary block lines" \
    '! grep -q "short test summary info" "$actual_file"'
diff "$expected_file" "$actual_file" >/dev/null && ok "pipeline output matches expected (set-equal)" \
    || bad "pipeline output differs from expected"

# ----------------------------------------------------------------------------
echo
echo "Task 4: categorization — comm-based attribution against a synthetic baseline"
# Replays the diff math the script does inline, against the same synthetic
# inputs we used in Task 3, plus a "current" set that introduces one new
# failure and loses one pre-existing failure (a fake "fixed" case).
baseline_file="$TMPDIR/baseline.txt"
current_file="$TMPDIR/current.txt"
cp "$expected_file" "$baseline_file"
cat > "$current_file" <<'EOF'
tests/test_a.py::test_one
tests/test_c.py::test_new
EOF

pre_count=$(comm -12 "$baseline_file" "$current_file" | wc -l | tr -d ' ')
new_list=$(comm -23 "$current_file" "$baseline_file" || true)
fixed_list=$(comm -13 "$current_file" "$baseline_file" || true)
new_count=$(printf '%s' "$new_list" | grep -c . || true)
fixed_count=$(printf '%s' "$fixed_list" | grep -c . || true)

check "pre_count == 1 (test_one is in both)" '[ "$pre_count" = "1" ]'
check "new_count == 1 (test_c is new)" '[ "$new_count" = "1" ]'
check "fixed_count == 2 (test_two + test_three gone)" '[ "$fixed_count" = "2" ]'
check "fixed_list includes test_two" 'printf "%s" "$fixed_list" | grep -q "test_two"'
check "fixed_list includes test_three" 'printf "%s" "$fixed_list" | grep -q "test_three"'
check "new_list includes test_c" 'printf "%s" "$new_list" | grep -q "test_c"'

# Empty current file: every baseline entry is "fixed", new_count = 0.
: > "$current_file"
new_count=$(comm -23 "$current_file" "$baseline_file" | wc -l | tr -d ' ')
pre_count=$(comm -12 "$baseline_file" "$current_file" | wc -l | tr -d ' ')
check "empty current → new_count == 0" '[ "$new_count" = "0" ]'
check "empty current → pre_count == 0" '[ "$pre_count" = "0" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 5: idempotency — a fresh baseline file is reused, not regenerated"
# Force the script into the "use cache" path with no venv needed (the existing
# baseline file is fresh enough that the cache wins).
state_dir="$TMPDIR/state"
mkdir -p "$state_dir"
echo "tests/test_a.py::test_one" > "$state_dir/pytest-baseline.txt"

# Make PYTEST_CMD a no-op so we'd notice if it actually got invoked; PYTEST_CWD
# pointing at /tmp means the script's `cd` succeeds, and PYTEST_FAKE_WORKTREE
# is set so it skips the detached git worktree dance.
out=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
      PYTEST_CMD=/bin/true PYTEST_CWD="$TMPDIR" PYTEST_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/pytest-baseline.sh" 2>&1 || true)
check "second run reuses cache (prints 'Using cached baseline')" \
    'echo "$out" | grep -qE "Using cached baseline"'
check "second run does NOT print 'Captured baseline'" \
    '! echo "$out" | grep -qE "^Captured baseline"'

# --regen flips the flag and forces the script through the capture path.
out=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
      PYTEST_CMD=/bin/true PYTEST_CWD="$TMPDIR" PYTEST_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/pytest-baseline.sh" --regen 2>&1 || true)
# /bin/true produces no FAILED lines, so the baseline file becomes empty.
check "--regen flows through capture (file now empty)" \
    '[ ! -s "$state_dir/pytest-baseline.txt" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 6: --print leaves the cache untouched and reports the current state"
# Recreate the cached baseline + verify --print reads it.
echo "tests/test_a.py::test_one" > "$state_dir/pytest-baseline.txt"
before=$(cat "$state_dir/pytest-baseline.txt")
out=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
      bash "$SCRIPT_DIR/pytest-baseline.sh" --print 2>&1 || true)
after=$(cat "$state_dir/pytest-baseline.txt")
check "--print reports the cached file path" \
    'echo "$out" | grep -q "pytest-baseline.txt"'
check "--print reports the count" \
    'echo "$out" | grep -qE "[0-9]+ pre-existing"'
check "--print does not modify the cache" \
    '[ "$before" = "$after" ]'

# --print with no cache: prints the "no baseline yet" hint and does not create
# one.
empty_state="$TMPDIR/empty_state"
mkdir -p "$empty_state"
out=$(PYTEST_BASELINE_PATH="$empty_state/pytest-baseline.txt" \
      bash "$SCRIPT_DIR/pytest-baseline.sh" --print 2>&1 || true)
check "--print on missing cache → 'no baseline yet'" \
    'echo "$out" | grep -qE "no baseline yet"'
check "--print on missing cache does not create it" \
    '[ ! -e "$empty_state/pytest-baseline.txt" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 7: pytest-compare.sh honors a fake pytest + faux baseline"
# Provide a fake pytest that prints two FAILED lines; one is pre-existing
# (in baseline), one is "new" (not in baseline).
cat > "$TMPDIR/fake_pytest" <<'EOF'
#!/usr/bin/env bash
cat <<INNER
FAILED tests/test_a.py::test_one - assert 1 == 2
FAILED tests/test_x.py::test_new - boom
=========== 2 failed in 0.01s ===========
INNER
exit 1
EOF
chmod +x "$TMPDIR/fake_pytest"

cat > "$state_dir/pytest-baseline.txt" <<'EOF'
tests/test_a.py::test_one
EOF

out=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
      PYTEST_CMD="$TMPDIR/fake_pytest" PYTEST_CWD="$TMPDIR" \
      PYTEST_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/pytest-compare.sh" 2>&1 || true)
check "compare prints attribution header" \
    'echo "$out" | grep -qE "pytest failure attribution"'
check "compare flags pre-existing failure" \
    'echo "$out" | grep -qE "pre-existing.*not your fault"'
check "compare flags the new failure" \
    'echo "$out" | grep -qE "tests/test_x\.py::test_new"'

# Re-run the same call and verify the comparator exits non-zero when there's
# at least one new failure.
ec=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
     PYTEST_CMD="$TMPDIR/fake_pytest" PYTEST_CWD="$TMPDIR" \
     PYTEST_FAKE_WORKTREE=1 \
     bash "$SCRIPT_DIR/pytest-compare.sh" >/dev/null 2>&1 || echo "$?")
check "compare exits 1 when new failures exist" \
    'echo "$ec" | grep -qE "^1$"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: --pre-existing-only suppresses the new/fixed listing"
out=$(PYTEST_BASELINE_PATH="$state_dir/pytest-baseline.txt" \
      PYTEST_CMD="$TMPDIR/fake_pytest" PYTEST_CWD="$TMPDIR" \
      PYTEST_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/pytest-compare.sh" --pre-existing-only 2>&1 || true)
check "--pre-existing-only prints the count" \
    'echo "$out" | grep -qE "pre-existing failures.*1"'
check "--pre-existing-only does NOT print the attribution header" \
    '! echo "$out" | grep -qE "pytest failure attribution"'
check "--pre-existing-only does NOT print NEW section" \
    '! echo "$out" | grep -qE "NEW.*needs fix"'

# ----------------------------------------------------------------------------
mv "$TMPDIR" /tmp/_pytest_baseline_test_artifacts >/dev/null 2>&1 || true
echo
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
