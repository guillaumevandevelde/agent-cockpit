#!/usr/bin/env bash
# Test harness for scripts/lib/measure_token_saver_lib.sh — the pure helpers
# behind scripts/measure-token-saver.sh.
#
# Covers:
#   1. apply_saver byte-stability: identical input → identical SHA-256 across
#      two calls; output contains the [SAVER:CAVEMAN] prelude + [SAVER:PONYTAIL]
#      tail; blank-line runs collapse to a single blank; diff-line dedup works
#      on a hand-crafted hunk.
#   2. parse_usage: reads the four documented `usage` fields separately
#      (input / cache_creation_input / cache_read_input / output) and emits
#      them in that order on stdout; handles missing fields as 0; errors on
#      unparseable JSON.
#   3. score_golden: returns `pass_tests=<0|1>` + `pass_diff=<0|1>` for a
#      worktree that contains (a) the dispatch.py revert, (b) the failing
#      tests pre-installed, and (c) the pytest invocation that exercises
#      them. The fixture builds a temporary pytest-stub returning a
#      deterministic exit code so we don't need a real pytest run.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib/measure_token_saver_lib.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: apply_saver is byte-stable and adds the saver markers"

cat > "$TMP/in.txt" <<'EOF'
line 1
line 2


line 3
EOF

HASH1=$( ( source "$LIB" 2>/dev/null && apply_saver "$TMP/in.txt" "$TMP/out1.txt" && sha256sum "$TMP/out1.txt" | awk '{print $1}' ) || echo "LIB_MISSING" )
HASH2=$( ( source "$LIB" 2>/dev/null && apply_saver "$TMP/in.txt" "$TMP/out2.txt" && sha256sum "$TMP/out2.txt" | awk '{print $1}' ) || echo "LIB_MISSING" )

check "apply_saver is available (lib sources cleanly)" 'source "$LIB" && type apply_saver >/dev/null 2>&1'
check "apply_saver produces output" '[ -s "$TMP/out1.txt" ]'
check "apply_saver is byte-stable (two runs → identical SHA-256)" '[ "$HASH1" = "$HASH2" ] && [ -n "$HASH1" ]'
check "output contains [SAVER:CAVEMAN] prelude" 'grep -qF "[SAVER:CAVEMAN]" "$TMP/out1.txt"'
check "output contains [SAVER:PONYTAIL] tail" 'grep -qF "[SAVER:PONYTAIL]" "$TMP/out1.txt"'
check "blank-line runs collapse (3+ newlines → 2)" '! grep -P "\\n{3,}" "$TMP/out1.txt"'

# Diff-line dedup — hand-crafted hunk with two identical +foo lines.
cat > "$TMP/diff.txt" <<'EOF'
context
+foo
+foo
+bar
EOF

( source "$LIB" 2>/dev/null && apply_saver "$TMP/diff.txt" "$TMP/diff.out.txt" )
check "diff-line dedup collapses two identical + lines to one" \
    '[ "$(grep -c "^+foo$" "$TMP/diff.out.txt")" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 2: parse_usage emits four separate usage values on stdout"

cat > "$TMP/usage.json" <<'EOF'
{"usage": {"input_tokens": 100, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 42, "output_tokens": 7}}
EOF

( source "$LIB" 2>/dev/null && parse_usage "$TMP/usage.json" > "$TMP/usage.out" ) || echo "PARSE_FAIL" > "$TMP/usage.out"
check "parse_usage exit 0" '[ ! -f "$TMP/usage.out" ] || ! grep -q "PARSE_FAIL" "$TMP/usage.out"'
check "line 1 = input_tokens (100)" '[ "$(sed -n 1p "$TMP/usage.out")" = "100" ]'
check "line 2 = cache_creation_input_tokens (5)" '[ "$(sed -n 2p "$TMP/usage.out")" = "5" ]'
check "line 3 = cache_read_input_tokens (42)" '[ "$(sed -n 3p "$TMP/usage.out")" = "42" ]'
check "line 4 = output_tokens (7)" '[ "$(sed -n 4p "$TMP/usage.out")" = "7" ]'

# Missing fields default to 0.
cat > "$TMP/usage_partial.json" <<'EOF'
{"usage": {"input_tokens": 9}}
EOF

( source "$LIB" 2>/dev/null && parse_usage "$TMP/usage_partial.json" > "$TMP/usage_partial.out" )
check "missing cache_creation defaults to 0" '[ "$(sed -n 2p "$TMP/usage_partial.out")" = "0" ]'
check "missing cache_read defaults to 0" '[ "$(sed -n 3p "$TMP/usage_partial.out")" = "0" ]'
check "missing output defaults to 0" '[ "$(sed -n 4p "$TMP/usage_partial.out")" = "0" ]'

# Unparseable JSON errors.
echo "not json" > "$TMP/bad.json"
out=$( ( source "$LIB" 2>/dev/null && parse_usage "$TMP/bad.json" ) 2>&1 ); rc=$?
check "unparseable JSON exits non-zero" '[ "$rc" -ne 0 ]'
check "unparseable JSON prints PARSE_ERROR" 'echo "$out" | grep -qE "PARSE_ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: score_golden returns pass_tests + pass_diff for a fixture"

# Build a worktree fixture with the dispatch.py revert + a fake pytest that
# exits 0 (simulating "tests pass").
mkdir -p "$TMP/wt/backend/app/kanban"
mkdir -p "$TMP/wt/backend/tests"
git -C "$TMP/wt" init -q -b master
git -C "$TMP/wt" config user.email t@t && git -C "$TMP/wt" config user.name t
# Initial commit so we can stage a diff.
echo "seed" > "$TMP/wt/seed.txt"
git -C "$TMP/wt" add seed.txt && git -C "$TMP/wt" commit -qm seed

# The pre-fix (failing) state: `r.max_sessions > 0`.
cat > "$TMP/wt/backend/app/kanban/dispatch.py" <<'EOF'
def _column_max_sessions():
    return {r.name: r.max_sessions for r in rows if r.max_sessions is not None and r.max_sessions > 0}
EOF
git -C "$TMP/wt" add backend/app/kanban/dispatch.py && git -C "$TMP/wt" commit -qm broken

# Now apply the 1-line fix so the diff is exactly the `>` → `>=` revert.
sed -i 's/r.max_sessions > 0/r.max_sessions >= 0/' "$TMP/wt/backend/app/kanban/dispatch.py"

# Fake the test target by stubbing PYTEST_CMD. We do that by sourcing the
# lib, then calling score_golden with a PYTEST_CMD override that points at
# a fixture script returning 0.

cat > "$TMP/fake_pytest.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/fake_pytest.sh"

( source "$LIB" 2>/dev/null && \
  PYTEST_CMD="$TMP/fake_pytest.sh" \
  BACKEND_DIR="$TMP/wt/backend" \
  score_golden "$TMP/wt" > "$TMP/score.out" )
check "score_golden exit 0" '[ -f "$TMP/score.out" ]'
check "score_golden line 1 = pass_tests=1" 'grep -q "^pass_tests=1" "$TMP/score.out"'
check "score_golden line 2 = pass_diff=1" 'grep -q "^pass_diff=1" "$TMP/score.out"'

# Now run with a failing fake pytest → pass_tests should flip to 0.
cat > "$TMP/fake_pytest_fail.sh" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$TMP/fake_pytest_fail.sh"

( source "$LIB" 2>/dev/null && \
  PYTEST_CMD="$TMP/fake_pytest_fail.sh" \
  BACKEND_DIR="$TMP/wt/backend" \
  score_golden "$TMP/wt" > "$TMP/score_fail.out" )
check "failing pytest → pass_tests=0" 'grep -q "^pass_tests=0" "$TMP/score_fail.out"'
check "pass_diff stays 1 (diff is still right)" 'grep -q "^pass_diff=1" "$TMP/score_fail.out"'

# ----------------------------------------------------------------------------
echo "Task 4: make_worktree + cleanup_worktree round-trip"

REPO="$TMP/repo"
mkdir -p "$REPO"
( cd "$REPO" && git init -q -b master && git config user.email t@t && git config user.name t && echo a > a.txt && git add a.txt && git commit -qm a )

( source "$LIB" 2>/dev/null && \
  WT=$( make_worktree "$REPO" "$TMP/wt-rt" ) && \
  echo "WT=$WT" > "$TMP/wt-rt.out" && \
  [ -d "$WT" ] && \
  cleanup_worktree "$REPO" "$WT" )

check "worktree directory was created" '[ -f "$TMP/wt-rt.out" ] && grep -q "^WT=" "$TMP/wt-rt.out"'
check "worktree directory was cleaned up" '[ ! -d "$TMP/wt-rt" ]'

# ----------------------------------------------------------------------------
echo "Task 5: compare isolates and counterbalances every Claude run"

mkdir -p "$TMP/bin"
cat > "$TMP/bin/claude" <<'EOF'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = "--version" ]; then
    echo "claude-stub 0"
    exit 0
fi
prompt=$(cat)
variant=baseline
case "$prompt" in
    *"[SAVER:CAVEMAN]"*) variant=with-saver ;;
esac
line=$(grep 'r.max_sessions' backend/app/kanban/dispatch.py || true)
printf '%s|%s|%s\n' "$PWD" "$variant" "$line" >> "$MEASURE_CLAUDE_LOG"
sed -i 's/r.max_sessions > 0/r.max_sessions >= 0/' backend/app/kanban/dispatch.py
printf '{"usage":{"input_tokens":10,"cache_creation_input_tokens":2,"cache_read_input_tokens":3,"output_tokens":4}}\n'
EOF
chmod +x "$TMP/bin/claude"
cat > "$TMP/bin/fake-pytest" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/bin/fake-pytest"

MEASURE_CLAUDE_LOG="$TMP/claude.log" \
PYTEST_CMD="$TMP/bin/fake-pytest" \
PATH="$TMP/bin:$PATH" \
bash "$SCRIPT_DIR/measure-token-saver.sh" compare > "$TMP/compare.out" 2> "$TMP/compare.err"

check "compare invokes exactly four isolated Claude runs" \
    '[ "$(wc -l < "$TMP/claude.log")" -eq 4 ]'
check "each compare run starts with the broken golden-task line" \
    '[ "$(grep -c "r.max_sessions > 0" "$TMP/claude.log")" -eq 4 ]'
check "each compare run uses a distinct worktree" \
    '[ "$(cut -d"|" -f1 "$TMP/claude.log" | sort -u | wc -l)" -eq 4 ]'
check "first trial runs baseline before with-saver" \
    '[ "$(sed -n 1p "$TMP/claude.log" | cut -d"|" -f2)" = baseline ] && [ "$(sed -n 2p "$TMP/claude.log" | cut -d"|" -f2)" = with-saver ]'
check "second trial reverses the variant order" \
    '[ "$(sed -n 3p "$TMP/claude.log" | cut -d"|" -f2)" = with-saver ] && [ "$(sed -n 4p "$TMP/claude.log" | cut -d"|" -f2)" = baseline ]'
check "compare reports both trials" \
    '[ "$(grep -c "| trial-[12]-baseline" "$TMP/compare.out")" -eq 2 ] && [ "$(grep -c "| trial-[12]-with-saver" "$TMP/compare.out")" -eq 2 ]'

# ----------------------------------------------------------------------------
echo "Summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]