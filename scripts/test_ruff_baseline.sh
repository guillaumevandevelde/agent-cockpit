#!/usr/bin/env bash
# Test harness for scripts/ruff-baseline.sh + scripts/ruff-compare.sh.
#
# Mirrors scripts/test_pytest_baseline.sh structure (PASS/FAIL counters,
# ok/bad/check helpers, `Total: $PASS passed, $FAIL failed` summary line,
# `[ "$FAIL" -eq 0 ]` final exit gate). Exercises every code path worth
# verifying without paying for a real detached-worktree git fetch on the
# box:
#
#   1. arg parsing — `--help` works, `--bad-arg` rejected.
#   2. error paths — missing baseline → exit 2, missing venv → exit 1.
#   3. parse filter — ruff's concise-format hit lines survive the
#      `grep -E ':[0-9]+:[0-9]+: '` pipeline that's used to build the
#      baseline. The footer lines (`Found N errors.`, `[*] N fixable …`)
#      get dropped because they don't match.
#   4. categorization — the diff logic correctly attributes hits to
#      "pre-existing", "new", or "fixed" against a synthetic baseline.
#   5. idempotency — a fresh baseline file is reused on the next call
#      instead of being regenerated.
#   6. --print mode — leaves the cache untouched, reports the current
#      state, doesn't create the cache when missing.
#   7. ruff-compare.sh honors a fake ruff + faux baseline end-to-end.
#   8. --pre-existing-only suppresses the new/fixed listing.
#   9. resolve_ruff_cmd — shared venv-resolution fallback (mirror of the
#      pytest tier tests; the lib has the same shape).
#
# Ruff invocation itself runs against a FAKE ruff binary (a shell stub
# that prints canned `--output-format=concise` output), enabled by setting
# RUFF_CMD + RUFF_CWD + RUFF_FAKE_WORKTREE before invoking the scripts
# under test. CI runs the real ruff (GitHub Actions `quality.yml`); this
# harness covers everything that doesn't need the production venv.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help on both scripts"
out1=$(bash "$SCRIPT_DIR/ruff-baseline.sh" --help 2>&1 || true)
check "ruff-baseline.sh --help mentions Usage" \
    'echo "$out1" | grep -qE "Usage:"'
check "ruff-baseline.sh --help mentions --regen" \
    'echo "$out1" | grep -qE "\-\-regen"'
# Regression guard for the `--output-format=concise` rationale added per
# kanban card 7678afc4… — without this grep a future docstring cleanup
# silently strips the user-facing explanation of why the script uses
# concise (vs. the default rich output) and why the filter is `:N:N:`.
check "ruff-baseline.sh --help mentions concise output rationale" \
    'echo "$out1" | grep -qE "concise"'

out2=$(bash "$SCRIPT_DIR/ruff-compare.sh" --help 2>&1 || true)
check "ruff-compare.sh --help mentions --pre-existing-only" \
    'echo "$out2" | grep -qE "\-\-pre-existing-only"'
check "ruff-compare.sh --help mentions baseline-required caveat" \
    'echo "$out2" | grep -qE "no baseline cached"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: error paths — unknown args + missing baseline + missing venv"
# Same shell-quoting lesson as test_pytest_baseline.sh:60-65: group with
# `(...)` so the `|| true` lands on the bash side, not the grep side.
check "ruff-baseline.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/ruff-baseline.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'
check "ruff-compare.sh rejects unknown arg" \
    '[ "$( ( bash "$SCRIPT_DIR/ruff-compare.sh" --totally-bogus 2>&1 || true ) | grep -c "unknown argument" )" -ge 1 ]'

# Force the comparator into the "no baseline" path by pointing it at a
# nonexistent path. Same shape as test_pytest_baseline.sh:71-74.
check "ruff-compare.sh exits 2 with no baseline cached" \
    '[ "$( ( RUFF_BASELINE_PATH=/nonexistent/baseline.txt bash "$SCRIPT_DIR/ruff-compare.sh" 2>&1 || true ) | grep -c "no baseline" )" -ge 1 ]'
check "missing baseline → exit code 2" \
    'RUFF_BASELINE_PATH=/nonexistent/baseline.txt bash "$SCRIPT_DIR/ruff-compare.sh" >/dev/null 2>&1; [ "$?" = "2" ]'

# Missing venv: point RUFF_CMD at something nonexistent and assert exit 1.
# Here the baseline path resolves to an existing file (touch'd below), so the
# script reaches the RUFF_CMD check and exits with the expected code 1.
TMPDIR=$(mktemp -d)
fake_baseline="$TMPDIR/baseline_real.txt"
: > "$fake_baseline"
check "missing venv → exit code 1" \
    'RUFF_CMD=/nonexistent/ruff RUFF_BASELINE_PATH="$fake_baseline" bash "$SCRIPT_DIR/ruff-compare.sh" >/dev/null 2>&1; [ "$?" = "1" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 3: parse filter — ruff concise-format hit lines survive the pipeline"
# `ruff check --output-format=concise` produces one line per hit in
# `<file>:<line>:<col>: <CODE> <message>` shape, plus a "Found N errors."
# footer and a "[*] N fixable …" hint. The filter `:N:N:` keeps the hit
# lines and drops both footer lines.
cat > "$TMPDIR/fake_ruff_output.txt" <<'EOF'
backend/app/foo.py:1:1: E401 [*] Multiple imports on one line
backend/app/foo.py:1:8: F401 [*] `os` imported but unused
backend/tests/test_bar.py:42:5: F841 Local variable `unused` is assigned to but never used
Found 3 errors.
[*] 2 fixable with the `--fix` option.
EOF
expected_file="$TMPDIR/expected.txt"
cat > "$expected_file" <<'EOF'
backend/app/foo.py:1:1: E401 [*] Multiple imports on one line
backend/app/foo.py:1:8: F401 [*] `os` imported but unused
backend/tests/test_bar.py:42:5: F841 Local variable `unused` is assigned to but never used
EOF

# Run the same pipeline the script uses.
actual_file="$TMPDIR/actual.txt"
grep -E ':[0-9]+:[0-9]+: ' "$TMPDIR/fake_ruff_output.txt" \
    | sort -u > "$actual_file"
check "pipeline produces 3 unique hit lines" \
    '[ "$(wc -l < "$actual_file")" = "3" ]'
check "pipeline preserves backend/app/foo.py:1:1: E401" \
    'grep -q "^backend/app/foo\.py:1:1: E401" "$actual_file"'
check "pipeline preserves backend/tests/test_bar.py:42:5: F841" \
    'grep -q "^backend/tests/test_bar\.py:42:5: F841" "$actual_file"'
check "pipeline drops the 'Found N errors.' footer" \
    '! grep -q "Found 3 errors" "$actual_file"'
check "pipeline drops the 'fixable' hint" \
    '! grep -q "fixable with" "$actual_file"'
diff "$expected_file" "$actual_file" >/dev/null && ok "pipeline output matches expected (set-equal)" \
    || bad "pipeline output differs from expected"

# ----------------------------------------------------------------------------
echo
echo "Task 4: categorization — comm-based attribution against a synthetic baseline"
# Replays the diff math the script does inline, against the same synthetic
# inputs we used in Task 3, plus a "current" set that introduces one new
# hit and loses one pre-existing hit (a fake "fixed" case).
baseline_file="$TMPDIR/baseline.txt"
current_file="$TMPDIR/current.txt"
cp "$expected_file" "$baseline_file"
cat > "$current_file" <<'EOF'
backend/app/foo.py:1:1: E401 [*] Multiple imports on one line
backend/app/baz.py:7:1: E302 expected 2 blank lines, found 1
EOF

pre_count=$(comm -12 "$baseline_file" "$current_file" | wc -l | tr -d ' ')
new_list=$(comm -23 "$current_file" "$baseline_file" || true)
fixed_list=$(comm -13 "$current_file" "$baseline_file" || true)
new_count=$(printf '%s' "$new_list" | grep -c . || true)
fixed_count=$(printf '%s' "$fixed_list" | grep -c . || true)

check "pre_count == 1 (foo.py:1:1 is in both)" '[ "$pre_count" = "1" ]'
check "new_count == 1 (baz.py:7:1 is new)" '[ "$new_count" = "1" ]'
check "fixed_count == 2 (foo.py:1:8 + test_bar.py:42:5 gone)" '[ "$fixed_count" = "2" ]'
check "new_list includes baz.py:7:1" 'printf "%s" "$new_list" | grep -q "baz\.py:7:1"'
check "fixed_list includes foo.py:1:8" 'printf "%s" "$fixed_list" | grep -q "foo\.py:1:8"'
check "fixed_list includes test_bar.py:42:5" 'printf "%s" "$fixed_list" | grep -q "test_bar\.py:42:5"'

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
echo "backend/app/foo.py:1:1: E401 [*] Multiple imports on one line" > "$state_dir/ruff-baseline.txt"

# Make RUFF_CMD a no-op so we'd notice if it actually got invoked; RUFF_CWD
# pointing at /tmp means the script's `cd` succeeds, and RUFF_FAKE_WORKTREE
# is set so it skips the detached git worktree dance.
out=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
      RUFF_CMD=/bin/true RUFF_CWD="$TMPDIR" RUFF_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/ruff-baseline.sh" 2>&1 || true)
check "second run reuses cache (prints 'Using cached baseline')" \
    'echo "$out" | grep -qE "Using cached baseline"'
check "second run does NOT print 'Captured baseline'" \
    '! echo "$out" | grep -qE "^Captured baseline"'

# --regen flips the flag and forces the script through the capture path.
out=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
      RUFF_CMD=/bin/true RUFF_CWD="$TMPDIR" RUFF_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/ruff-baseline.sh" --regen 2>&1 || true)
# /bin/true produces no concise-format lines, so the baseline file becomes empty.
check "--regen flows through capture (file now empty)" \
    '[ ! -s "$state_dir/ruff-baseline.txt" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 6: --print leaves the cache untouched and reports the current state"
# Recreate the cached baseline + verify --print reads it.
echo "backend/app/foo.py:1:1: E401 [*] Multiple imports on one line" > "$state_dir/ruff-baseline.txt"
before=$(cat "$state_dir/ruff-baseline.txt")
out=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
      bash "$SCRIPT_DIR/ruff-baseline.sh" --print 2>&1 || true)
after=$(cat "$state_dir/ruff-baseline.txt")
check "--print reports the cached file path" \
    'echo "$out" | grep -q "ruff-baseline.txt"'
check "--print reports the count" \
    'echo "$out" | grep -qE "[0-9]+ pre-existing"'
check "--print does not modify the cache" \
    '[ "$before" = "$after" ]'

# --print with no cache: prints the "no baseline yet" hint and does not create
# one.
empty_state="$TMPDIR/empty_state"
mkdir -p "$empty_state"
out=$(RUFF_BASELINE_PATH="$empty_state/ruff-baseline.txt" \
      bash "$SCRIPT_DIR/ruff-baseline.sh" --print 2>&1 || true)
check "--print on missing cache → 'no baseline yet'" \
    'echo "$out" | grep -qE "no baseline yet"'
check "--print on missing cache does not create it" \
    '[ ! -e "$empty_state/ruff-baseline.txt" ]'

# ----------------------------------------------------------------------------
echo
echo "Task 7: ruff-compare.sh honors a fake ruff + faux baseline"
# Provide a fake ruff that prints two concise-format lines; one is
# pre-existing (in baseline), one is "new" (not in baseline).
cat > "$TMPDIR/fake_ruff" <<'EOF'
#!/usr/bin/env bash
cat <<INNER
backend/app/foo.py:1:1: E401 [*] Multiple imports on one line
backend/app/zzz_new.py:99:9: F401 [*] `foo` imported but unused
Found 2 errors.
INNER
exit 1
EOF
chmod +x "$TMPDIR/fake_ruff"

cat > "$state_dir/ruff-baseline.txt" <<'EOF'
backend/app/foo.py:1:1: E401 [*] Multiple imports on one line
EOF

out=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
      RUFF_CMD="$TMPDIR/fake_ruff" RUFF_CWD="$TMPDIR" \
      RUFF_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/ruff-compare.sh" 2>&1 || true)
check "compare prints attribution header" \
    'echo "$out" | grep -qE "ruff hit attribution"'
check "compare flags pre-existing failure" \
    'echo "$out" | grep -qE "pre-existing.*not your fault"'
check "compare flags the new failure" \
    'echo "$out" | grep -qE "backend/app/zzz_new\.py:99:9: F401"'
check "compare drops the 'Found 2 errors.' footer (filter works)" \
    '! echo "$out" | grep -qE "Found 2 errors"'

# Re-run the same call and verify the comparator exits non-zero when there's
# at least one new hit.
ec=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
     RUFF_CMD="$TMPDIR/fake_ruff" RUFF_CWD="$TMPDIR" \
     RUFF_FAKE_WORKTREE=1 \
     bash "$SCRIPT_DIR/ruff-compare.sh" >/dev/null 2>&1 || echo "$?")
check "compare exits 1 when new hits exist" \
    'echo "$ec" | grep -qE "^1$"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: --pre-existing-only suppresses the new/fixed listing"
out=$(RUFF_BASELINE_PATH="$state_dir/ruff-baseline.txt" \
      RUFF_CMD="$TMPDIR/fake_ruff" RUFF_CWD="$TMPDIR" \
      RUFF_FAKE_WORKTREE=1 \
      bash "$SCRIPT_DIR/ruff-compare.sh" --pre-existing-only 2>&1 || true)
check "--pre-existing-only prints the count" \
    'echo "$out" | grep -qE "pre-existing failures.*1"'
check "--pre-existing-only does NOT print the attribution header" \
    '! echo "$out" | grep -qE "ruff hit attribution"'
check "--pre-existing-only does NOT print NEW section" \
    '! echo "$out" | grep -qE "NEW.*needs fix"'

# ----------------------------------------------------------------------------
echo
echo "Task 9: resolve_ruff_cmd — shared venv-resolution fallback (mirror of pytest)"
# Same tier-1..5 chain as scripts/test_pytest_baseline.sh Task 9 — the lib
# has the same shape, so we exercise the same code paths and only swap the
# tool name (ruff instead of pytest, RUFF_CMD instead of PYTEST_CMD).
LIB="$SCRIPT_DIR/lib/resolve-ruff-cmd.sh"
check "resolve-ruff-cmd.sh lib exists" '[ -f "$LIB" ]'
check "ruff-baseline.sh sources the shared lib" \
    'grep -qE "source.*lib/resolve-ruff-cmd\.sh" "$SCRIPT_DIR/ruff-baseline.sh"'
check "ruff-compare.sh sources the shared lib" \
    'grep -qE "source.*lib/resolve-ruff-cmd\.sh" "$SCRIPT_DIR/ruff-compare.sh"'

BASH_BIN="$(command -v bash)"
run_resolve() {
    local rcmd="$1" bdir="$2" pathval="$3" shared="${4:-}"
    env -i PATH="$pathval" RUFF_CMD="$rcmd" "$BASH_BIN" -c '
        set -u
        source "'"$LIB"'"
        resolve_ruff_cmd "'"$bdir"'" "'"$shared"'"
        rc=$?
        echo "RC=$rc"
        echo "RESULT=${RUFF_CMD:-}"
    ' 2>&1
}

fake_backend_no_venv="$TMPDIR/fake_backend_no_venv"
mkdir -p "$fake_backend_no_venv"

fake_backend_with_venv="$TMPDIR/fake_backend_with_venv"
mkdir -p "$fake_backend_with_venv/venv/bin"
cat > "$fake_backend_with_venv/venv/bin/ruff" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fake_backend_with_venv/venv/bin/ruff"

nonexistent_shared="$TMPDIR/nonexistent_shared_venv/ruff"

# Tier 1: explicit RUFF_CMD wins over everything.
out=$(run_resolve "/explicit/override/ruff" "$fake_backend_no_venv" "/usr/bin:/bin")
check "tier 1: explicit RUFF_CMD short-circuits (RC=0)" \
    'echo "$out" | grep -qE "^RC=0$"'
check "tier 1: explicit RUFF_CMD value preserved verbatim" \
    'echo "$out" | grep -qE "^RESULT=/explicit/override/ruff$"'

# Tier 2: worktree-local venv wins when RUFF_CMD is unset.
out=$(run_resolve "" "$fake_backend_with_venv" "/usr/bin:/bin")
check "tier 2: worktree-local venv resolves (RC=0)" \
    'echo "$out" | grep -qE "^RC=0$"'
check "tier 2: resolves to \$backend_dir/venv/bin/ruff" \
    'echo "$out" | grep -qE "^RESULT=$fake_backend_with_venv/venv/bin/ruff$"'

# Tier 3: no worktree-local venv, real default shared main-checkout venv on
# this box — the literal card scenario. Skipped gracefully if this box has
# no shared venv.
if [ -x /home/vdvgu/claude-cockpit/backend/venv/bin/ruff ]; then
    out=$(run_resolve "" "$fake_backend_no_venv" "/usr/bin:/bin")
    check "tier 3: falls back to shared main-checkout venv (RC=0)" \
        'echo "$out" | grep -qE "^RC=0$"'
    check "tier 3: resolves to the shared main-checkout venv path" \
        'echo "$out" | grep -qE "^RESULT=/home/vdvgu/claude-cockpit/backend/venv/bin/ruff$"'
else
    echo "  (skipped tier 3 — no shared venv on this box)"
fi

# Tier 4: no worktree-local venv, injected-nonexistent shared venv, bare
# `ruff` present on PATH.
path_with_ruff="$TMPDIR/path_with_ruff"
mkdir -p "$path_with_ruff"
cat > "$path_with_ruff/ruff" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$path_with_ruff/ruff"
out=$(run_resolve "" "$fake_backend_no_venv" "$path_with_ruff:/usr/bin:/bin" "$nonexistent_shared")
check "tier 4: falls back to PATH (RC=0)" \
    'echo "$out" | grep -qE "^RC=0$"'
check "tier 4: resolves to the PATH ruff" \
    'echo "$out" | grep -qE "^RESULT=$path_with_ruff/ruff$"'

# Tier 5 (none found): no worktree-local venv, injected-nonexistent shared
# venv, empty PATH — resolution fails with a descriptive "tried" hint.
empty_path="$TMPDIR/empty_path_for_resolve"
mkdir -p "$empty_path"
out=$(run_resolve "" "$fake_backend_no_venv" "$empty_path" "$nonexistent_shared")
check "none found: exits non-zero" \
    '! echo "$out" | grep -qE "^RC=0$"'
check "none found: prints 'ruff not found' hint" \
    'echo "$out" | grep -qE "ruff not found"'
check "none found: hint names \$RUFF_CMD" \
    'echo "$out" | grep -qE "RUFF_CMD .unset"'
check "none found: hint names the worktree-local venv path" \
    'echo "$out" | grep -qE "$fake_backend_no_venv/venv/bin/ruff"'
check "none found: hint names the injected shared venv path" \
    'echo "$out" | grep -qE "nonexistent_shared_venv/ruff"'
check "none found: hint names PATH fallback" \
    'echo "$out" | grep -qE "on PATH"'

# ----------------------------------------------------------------------------
mv "$TMPDIR" /tmp/_ruff_baseline_test_artifacts >/dev/null 2>&1 || true
echo
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
