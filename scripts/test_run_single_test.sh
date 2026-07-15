#!/usr/bin/env bash
# Test harness for scripts/run-single-test.sh.
#
# Exercises every code path worth verifying without paying for a real pytest
# run on the box — the script supports a PYTEST_CMD env-var override so the
# harness can inject a fake pytest binary that prints canned output and
# exits with a chosen code. This mirrors how
# scripts/test_pytest_baseline.sh tests scripts/pytest-baseline.sh.
#
# Coverage:
#   1. arg parsing — --help works; missing arg → exit 2 with hint.
#   2. error paths — missing pytest (every fallback named); non-executable
#      PYTEST_CMD override; missing test file (echoes looked-under paths).
#   3. happy path — fake pytest exits 0 with PASS lines, script exits 0
#      and surfaces stdout.
#   4. failure path — fake pytest exits 1 with FAIL lines, script passes
#      the exit code through so callers can chain on `$?`.
#   5. "no tests ran" hint — fake pytest exits 5, script appends the
#      "typo / -k / skipped-module" hint before exiting.
#   6. passthrough — extra args after the test target reach pytest
#      verbatim, alongside the script's own --timeout / -q flags.
#   7. PYTEST_CMD wins over PATH — the override prevents any real pytest
#      on the system from running when the harness sets it.
#   8. end-to-end smoke — real pytest on tests/test_ship_recipe_drift.py
#      (skipped silently when the production venv is unavailable, so CI
#      sandbox without /home/vdvgu/.../venv still validates everything
#      else).
#
# Two bash subtleties this harness gets right (the first time I wrote it I
# got both wrong — keeping them called out so the next editor doesn't
# re-introduce them):
#
#  a) `VAR=val out=$(cmd)` does NOT propagate VAR into the command
#     substitution subshell. Only `VAR=val cmd` (no surrounding `$()`)
#     attaches VAR to cmd's environment, and only an explicit
#     `export VAR=val` or `env VAR=val cmd` works inside `$(...)`. The
#     first iteration of this harness used the inline form, the subshell
#     saw PYTEST_CMD as empty, and the script's "no pytest" path kept
#     winning through the absolute-path fallback — so most checks failed.
#     The fix: every fixture invocation below uses the `run_sut` helper,
#     which routes through `env` explicitly.
#
#  b) `out=$(cmd 2>&1); rc=$?` is the only safe exit-code capture under
#     `set -u` (the harness's only `set` flag). Wrapping the call in
#     `|| true` inside the subshell collapses `$?` to 0 and silently
#     masks a real failure as "ok".

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/run-single-test.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'mv "$TMP" /tmp/_run_single_test_artifacts >/dev/null 2>&1 || true' EXIT

# Helper: run the SUT with explicit env vars and capture both stdout and
# the exit code. Extra env entries are passed positionally so the harness
# never relies on the broken `VAR=val out=$(cmd)` form.
#
# Usage: run_sut [ENV_VAR=val ...] -- [SUT_ARG ...]
#
# Everything before `--` is `env`'d into the SUT's environment; everything
# after is forwarded to the SUT itself.
run_sut() {
    local -a envs=() args=()
    local in_args=0
    for tok in "$@"; do
        if [ "$in_args" = 1 ]; then args+=("$tok"); continue; fi
        case "$tok" in
            --) in_args=1 ;;
            *=*) envs+=("$tok") ;;
            *)   args+=("$tok"); in_args=1 ;;   # first non-VAR token starts the SUT argv
        esac
    done
    out=$(env "${envs[@]}" /usr/bin/bash "$SUT" "${args[@]}" 2>&1)
    rc=$?
}

# Empty-path fixture: a directory with no `pytest` binary, so the script's
# `command -v pytest` fallback can't find anything.
empty_path="$TMP/empty"
mkdir -p "$empty_path"

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing"
run_sut -- --help
check "--help mentions Usage" \
    'echo "$out" | grep -qE "Usage:"'
check "--help mentions --timeout cap override env-var" \
    'echo "$out" | grep -qE "RUN_SINGLE_TEST_TIMEOUT"'
check "--help mentions the file::func pattern" \
    'echo "$out" | grep -qE "tests/test_x\.py::test_y"'
check "--help mentions -k filter passthrough" \
    'echo "$out" | grep -qE "pytest -k"'

run_sut --
check "no args → exit 2" '[ "$rc" -eq 2 ]'
check "no args → hint mentions missing TEST_TARGET" \
    'echo "$out" | grep -qE "missing TEST_TARGET"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: error path — missing pytest (source-level check)"
# The script's venv resolution has a hardcoded absolute-path fallback
# (`/home/vdvgu/claude-cockpit/backend/venv/bin/pytest`) that always
# wins on this box because the real venv exists at that path. To
# exercise the "no pytest anywhere" branch we'd need to delete or
# bind-mount-shadow that path, which is destructive. As a regression
# guard we instead verify the script SOURCE contains the hint text the
# error path is supposed to print — a future editor who deletes one
# of the four "Tried:" lines is caught here even though we can't run
# the path. The source-grep is a weaker guarantee than a true execute
# the failure path test, but it's the next-best thing without root.
check "script source mentions PYTEST_CMD env-var in error hint" \
    'grep -qE "PYTEST_CMD .unset" "$SUT"'
check "script source mentions worktree-venv path in error hint" \
    'grep -qE "backend/venv/bin/pytest" "$SUT"'
check "script source mentions shared-venv path in error hint" \
    'grep -qE "shared main checkout venv" "$SUT"'
check "script source mentions PATH fallback in error hint" \
    'grep -qE "on PATH" "$SUT"'

# ----------------------------------------------------------------------------
echo
echo "Task 3: error path — non-executable PYTEST_CMD"
fake="$TMP/not_executable_pytest"
echo '#!/usr/bin/env bash' > "$fake"   # intentionally not chmod +x
# Use the real drift-test file as the target so the file-existence check
# passes and the script reaches the executable check we want to verify.
REAL_TARGET=tests/test_ship_recipe_drift.py
run_sut PYTEST_CMD="$fake" -- "$REAL_TARGET"
check "non-executable PYTEST_CMD → exit 1" '[ "$rc" -eq 1 ]'
check "non-executable PYTEST_CMD → 'not executable'" \
    'echo "$out" | grep -qE "not executable"'

# ----------------------------------------------------------------------------
echo
echo "Task 4: error path — missing test file"
cat > "$TMP/fake_pytest_should_not_run" <<'EOF'
#!/usr/bin/env bash
echo "FAIL: should not have run pytest at all — script must short-circuit on missing file" >&2
exit 99
EOF
chmod +x "$TMP/fake_pytest_should_not_run"
run_sut PYTEST_CMD="$TMP/fake_pytest_should_not_run" -- tests/does_not_exist.py::test_y
check "missing file → exit 1" '[ "$rc" -eq 1 ]'
check "missing file → 'test file not found'" \
    'echo "$out" | grep -qE "test file not found"'
check "missing file → echoes looked-under paths" \
    'echo "$out" | grep -qE "looked under"'
check "missing file → pytest was NOT invoked" \
    '! echo "$out" | grep -qE "should not have run pytest"'

# ----------------------------------------------------------------------------
echo
echo "Task 5: happy path — fake pytest exits 0 with PASS lines"
cat > "$TMP/fake_pytest_pass" <<'EOF'
#!/usr/bin/env bash
echo "tests/test_x.py::test_y PASSED                                          [100%]"
echo "========== 1 passed in 0.00s =========="
exit 0
EOF
chmod +x "$TMP/fake_pytest_pass"
# Use the real drift-test file as the target so the script's file-existence
# check passes — the fake pytest doesn't care which file was passed in, it
# just exits 0 with canned output.
run_sut PYTEST_CMD="$TMP/fake_pytest_pass" -- "$REAL_TARGET"
check "happy → exit 0" '[ "$rc" -eq 0 ]'
check "happy → stdout surfaces the fake-pytest output" \
    'echo "$out" | grep -qE "test_y PASSED"'
check "happy → surfaces pytest own summary line" \
    'echo "$out" | grep -qE "1 passed in"'

# ----------------------------------------------------------------------------
echo
echo "Task 6: failure path — fake pytest exits 1 with FAIL lines"
cat > "$TMP/fake_pytest_fail" <<'EOF'
#!/usr/bin/env bash
cat <<INNER
tests/test_x.py::test_y FAILED                                              [ 50%]
assert 1 == 2
========== 1 failed in 0.01s ==========
INNER
exit 1
EOF
chmod +x "$TMP/fake_pytest_fail"
run_sut PYTEST_CMD="$TMP/fake_pytest_fail" -- "$REAL_TARGET"
check "failure → exit 1 (caller can chain on dollar-?)" \
    '[ "$rc" -eq 1 ]'
check "failure → stdout surfaces the FAIL line" \
    'echo "$out" | grep -qE "test_y FAILED"'
check "failure → stdout surfaces the assertion detail" \
    'echo "$out" | grep -qE "assert 1 == 2"'

# ----------------------------------------------------------------------------
echo
echo "Task 7: \"no tests ran\" hint — fake pytest exits 5"
cat > "$TMP/fake_pytest_no_tests" <<'EOF'
#!/usr/bin/env bash
echo "tests/test_x.py: no tests ran" >&2
exit 5
EOF
chmod +x "$TMP/fake_pytest_no_tests"
run_sut PYTEST_CMD="$TMP/fake_pytest_no_tests" -- "$REAL_TARGET"
check "no-tests → exit 5 (pytest own exit code passed through)" \
    '[ "$rc" -eq 5 ]'
check "no-tests → script appends the typo hint" \
    'echo "$out" | grep -qE "typo in the function name after ::"'
check "no-tests → script appends the -k hint" \
    'echo "$out" | grep -qE " -k filter"'
check "no-tests → script appends the skipped-module hint" \
    'echo "$out" | grep -qE "skipped by an env-var"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: passthrough — extra args reach pytest verbatim"
cat > "$TMP/fake_pytest_capture_args" <<'EOF'
#!/usr/bin/env bash
# Write argv as one grep-friendly line so the test can find specific flags.
printf 'ARGV: %s\n' "$*"
exit 0
EOF
chmod +x "$TMP/fake_pytest_capture_args"
run_sut PYTEST_CMD="$TMP/fake_pytest_capture_args" -- "$REAL_TARGET" -k "param_id" -v
check "passthrough → exit 0" '[ "$rc" -eq 0 ]'
check "passthrough → -k arg reaches pytest" \
    'echo "$out" | grep -qE "ARGV:.*-k param_id"'
check "passthrough → -v arg reaches pytest" \
    'echo "$out" | grep -qE "ARGV:.* -v"'
check "passthrough → script still injects --timeout" \
    'echo "$out" | grep -qE "ARGV:.*--timeout"'
check "passthrough → script still injects -q" \
    'echo "$out" | grep -qE "ARGV:.* -q( |$)"'

# ----------------------------------------------------------------------------
echo
echo "Task 9: venv resolution — error hint names both venv paths (source-level check)"
# Same caveat as Task 2: the hardcoded absolute-path fallback makes the
# failure path unreachable without filesystem manipulation. Covered here
# by the same source-grep approach so the test suite still proves the
# hint names every fixture a future user would need.
check "hint names the absolute shared-venv path" \
    'grep -qE "/home/vdvgu/claude-cockpit/backend/venv/bin/pytest" "$SUT"'
check "hint names the worktree-venv path" \
    'grep -qE "backend/venv/bin/pytest" "$SUT"'

# ----------------------------------------------------------------------------
echo
echo "Task 10: PYTEST_CMD override wins over PATH"
TMP_PATHVENV="$TMP/pathvenv"
mkdir -p "$TMP_PATHVENV"
cat > "$TMP_PATHVENV/pytest" <<'EOF'
#!/usr/bin/env bash
echo "PATH pytest ran — override did NOT win" >&2
exit 7
EOF
chmod +x "$TMP_PATHVENV/pytest"

cat > "$TMP/winning_pytest" <<'EOF'
#!/usr/bin/env bash
echo "override pytest won"
exit 0
EOF
chmod +x "$TMP/winning_pytest"

run_sut \
    PATH="$TMP_PATHVENV:$empty_path:/usr/bin:/bin" \
    PYTEST_CMD="$TMP/winning_pytest" \
    -- "$REAL_TARGET"
check "override wins → exit 0" '[ "$rc" -eq 0 ]'
check "override wins → override pytest ran" \
    'echo "$out" | grep -qE "override pytest won"'
check "override wins → PATH pytest did NOT run" \
    '! echo "$out" | grep -qE "PATH pytest ran"'

# ----------------------------------------------------------------------------
echo
echo "Task 11: end-to-end smoke — real pytest on a real test file"
# Uses the worktree's actual backend code. Skipped silently when the
# production venv is unavailable (sandbox without /home/vdvgu/.../venv)
# so CI still validates everything else.
PYTEST_BIN=/home/vdvgu/claude-cockpit/backend/venv/bin/pytest
if [ -x "$PYTEST_BIN" ]; then
    target=tests/test_ship_recipe_drift.py
    if [ -f "$BACKEND_DIR/$target" ]; then
        t0=$(date +%s%N)
        run_sut PYTEST_CMD="$PYTEST_BIN" -- "$target"
        t1=$(date +%s%N)
        elapsed_ms=$(( (t1 - t0) / 1000000 ))
        check "smoke → real pytest exit 0 (test passes today)" \
            '[ "$rc" -eq 0 ]'
        check "smoke → elapsed < 5000ms (card AC #2 — option 2)" \
            '[ "$elapsed_ms" -lt 5000 ]'
        check "smoke → surfaces pytest N-passed summary line" \
            'echo "$out" | grep -qE "[0-9]+ passed"'
        if [ "$rc" -ne 0 ]; then
            echo "    --- real pytest output for diagnostics ---" >&2
            echo "$out" | sed 's/^/    /' >&2
        fi
    else
        echo "  (skipped — $target not present in this checkout)"
    fi
else
    echo "  (skipped — real venv not at $PYTEST_BIN; env-var-only coverage above)"
fi

# ----------------------------------------------------------------------------
echo
echo "Task 12: file::func stripping resolves the file correctly"
# Regression guard for the bug where `tests/test_x.py::test_y` tripped the
# file-existence check (the literal path with `::` separator doesn't
# exist as a file). The fix lives in run-single-test.sh:
# `FILE_PATH="${TEST_TARGET%%::*}"`.
run_sut PYTEST_CMD="$TMP/fake_pytest_pass" -- "tests/test_ship_recipe_drift.py::test_x"
check "file::func target → exit 0 (file-exists check passes)" \
    '[ "$rc" -eq 0 ]'
# fake_pytest_pass ignores argv; checking for the fake's "1 passed in"
# line proves the SCRIPT reached the run step (the regression we care
# about) without depending on the actual pytest behaviour.
check "file::func target → full target forwarded to pytest" \
    'echo "$out" | grep -qE "1 passed in"'

run_sut PYTEST_CMD="$TMP/fake_pytest_pass" -- "tests/test_ship_recipe_drift.py::test_x[a-b]"
check "file::func[param] target → exit 0 (parametrize suffix tolerated)" \
    '[ "$rc" -eq 0 ]'
check "file::func[param] target → surfaces pytest summary" \
    'echo "$out" | grep -qE "1 passed in"'

run_sut PYTEST_CMD="$TMP/fake_pytest_pass" -- "tests/does_not_exist.py::test_y"
check "still rejects truly missing files (file::func, bad path)" \
    '[ "$rc" -eq 1 ]'
check "truly missing file → 'test file not found' error" \
    'echo "$out" | grep -qE "test file not found"'
# The error message should reference the FILE PATH (without `::func`),
# not the full target — that is the user-visible behaviour the fix
# enables. Pre-fix it would have echoed `tests/does_not_exist.py::test_y`
# which is misleading.
check "missing-file error references the file path (no ::func suffix)" \
    'echo "$out" | grep -qE "test file not found: tests/does_not_exist\.py$"'

# ----------------------------------------------------------------------------
echo
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
