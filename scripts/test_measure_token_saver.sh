#!/usr/bin/env bash
# Test harness for scripts/lib/measure_token_saver_lib.sh + the
# scripts/measure-token-saver.sh driver (the counterbalanced smoke).
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
#   3. score_golden: returns `pass_tests=<0|1>` for a worktree that contains
#      (a) the dispatch.py revert, (b) the failing tests pre-installed, and
#      (c) the pytest invocation that exercises them. The fixture builds a
#      temporary pytest-stub returning a deterministic exit code so we don't
#      need a real pytest run. No pass_diff column — see kaart 0a3ee4c9…
#      and docs/cockpit/prompt-injectors-decision.md §"Over pass_diff" for
#      why the text-form check was removed in favour of pass_tests alone.
#   4. resolve_measurement_base_ref + prepare_golden_revert: baseline ref
#      resolves to origin/master when present, falls back to master, then to
#      HEAD; prepare_golden_revert fails closed on a worktree whose
#      dispatch.py lacks the expected fixed line and succeeds on one that
#      has it.
#   5. compare smoke: driver runs four isolated Claude invocations across
#      two counterbalanced trials, every run in its own scratch worktree
#      seeded from the resolved base ref, and the output table reports all
#      four rows. Uses a stub claude CLI that logs PWD/variant/golden-task
#      line per run, so no network or real Claude is required.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib/measure_token_saver_lib.sh"

PASS=0; FAIL=0
EXPECTED_BAD_AT_END=0
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

# --- render_card_prompt (production build_card_prompt) -------------------
# These checks drive the production assembler, so they need the real
# backend/ package importable. Derive the repo root from the lib's own path:
# $LIB is <repo>/scripts/lib/measure_token_saver_lib.sh, so dirname is
# <repo>/scripts/lib and ../.. is <repo>. An earlier revision used `/..`
# (one level short), which made the guard below fail in EVERY checkout and
# silently fell through to a hardcoded worktree path that only existed in
# the authoring session — 7 checks passed there and failed everywhere else.
# No fallback now: an unresolvable root must fail loudly.
REPO_ROOT_FOR_TESTS="$(cd "$(dirname "$LIB")/../.." 2>/dev/null && pwd)"
check "test repo root resolves to a real checkout (no hardcoded fallback)" \
    '[ -n "$REPO_ROOT_FOR_TESTS" ] && [ -d "$REPO_ROOT_FOR_TESTS/backend/app/kanban" ]'

cat > "$TMP/inject_in.txt" <<'EOF'
the user prompt body sits here
EOF

INJ_HASH1=$( ( cd "$REPO_ROOT_FOR_TESTS" && source "$LIB" && render_card_prompt "$TMP/inject_in.txt" "$TMP/inj_out1.txt" 1 && sha256sum "$TMP/inj_out1.txt" | awk '{print $1}' ) || echo "INJ_FAIL" )
INJ_HASH2=$( ( cd "$REPO_ROOT_FOR_TESTS" && source "$LIB" && render_card_prompt "$TMP/inject_in.txt" "$TMP/inj_out2.txt" 1 && sha256sum "$TMP/inj_out2.txt" | awk '{print $1}' ) || echo "INJ_FAIL" )
( cd "$REPO_ROOT_FOR_TESTS" && source "$LIB" && render_card_prompt "$TMP/inject_in.txt" "$TMP/inj_off.txt" 0 ) || true

check "render_card_prompt is available (lib sources cleanly)" 'source "$LIB" && type render_card_prompt >/dev/null 2>&1'
check "render_card_prompt produces output from the production assembler" '[ -s "$TMP/inj_out1.txt" ]'
check "render_card_prompt is byte-stable across two runs (same inputs → same SHA-256)" \
    '[ "$INJ_HASH1" = "$INJ_HASH2" ] && [ -n "$INJ_HASH1" ] && [ "$INJ_HASH1" != "INJ_FAIL" ]'
check "injector arm carries the verbatim Caveman attribution header" \
    'grep -qF "github.com/JuliusBrussee/caveman" "$TMP/inj_out1.txt"'
check "injector arm carries the verbatim Ponytail attribution header" \
    'grep -qF "github.com/DietrichGebert/ponytail" "$TMP/inj_out1.txt"'
check "injector arm preserves the original task body" \
    'grep -qF "the user prompt body sits here" "$TMP/inj_out1.txt"'
# Production order (dispatch.py::build_card_prompt): persona, then BOTH
# injector slices, then the card body. The predecessor hand-assembled the
# preamble and put Ponytail AFTER the body — a wrong prefix, and cache_read
# is a prefix property. This assertion is what catches that regression.
check "injector slices sit between the persona and the card body (production order)" \
    'python3 - "$TMP/inj_out1.txt" <<PYEOF
import sys
t = open(sys.argv[1]).read()
cav = t.find("Respond terse like smart caveman")
pon = t.find("You are a lazy senior developer")
body = t.find("the user prompt body sits here")
sys.exit(0 if 0 <= cav < pon < body else 1)
PYEOF'
check "baseline arm renders the same scaffolding without either slice" \
    '[ -s "$TMP/inj_off.txt" ] && grep -qF "the user prompt body sits here" "$TMP/inj_off.txt" \
     && ! grep -qF "github.com/JuliusBrussee/caveman" "$TMP/inj_off.txt" \
     && ! grep -qF "github.com/DietrichGebert/ponytail" "$TMP/inj_off.txt"'
# Size guard: the two arms differ by the verbatim slice only (~11 KB), not
# by the ~160-byte apply_saver proxy.
check "injector arm exceeds the baseline arm by at least 4 KB (verbatim slice, not the proxy)" \
    '[ "$(( $(wc -c < "$TMP/inj_out1.txt") - $(wc -c < "$TMP/inj_off.txt") ))" -gt 4096 ]'
# Fail-closed: an unresolvable repo root must surface as a non-zero exit +
# stderr, never as a silent stub prompt.
FAKE_LIB_DIR="$TMP/fake-lib"
mkdir -p "$FAKE_LIB_DIR"
cp "$LIB" "$FAKE_LIB_DIR/measure_token_saver_lib.sh"
empty_rc=0
empty_err=$( ( source "$FAKE_LIB_DIR/measure_token_saver_lib.sh" && render_card_prompt "$TMP/inject_in.txt" "$TMP/inj_out_fail.txt" 1 ) 2>&1 ) || empty_rc=$?
check "render_card_prompt exits non-zero when backend/ is not reachable from the lib" '[ "$empty_rc" -ne 0 ]'
check "render_card_prompt fail-closed error names the unresolvable repo root" \
    'echo "$empty_err" | grep -qE "cannot resolve repo root"'

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
echo "Task 3: score_golden returns pass_tests (and only pass_tests) for a fixture"

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
# Regression guard (kaart 0a3ee4c9…): the old pass_diff column scored 0 in
# every run because the text-form check couldn't follow equivalent
# rewrites. A future regression that re-adds it as a side effect would
# also need to ship a behavioural fix to make it useful; this assertion
# fails in the old form (which emitted `pass_diff=1`) and pins the
# single-column contract.
check "score_golden emits no pass_diff line (regression guard for kaart 0a3ee4c9…)" \
    '! grep -q "^pass_diff=" "$TMP/score.out"'
check "score_golden output is exactly one line (pass_tests only)" \
    '[ "$(wc -l < "$TMP/score.out")" -eq 1 ]'

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
check "failing pytest still emits no pass_diff line (regression guard)" \
    '! grep -q "^pass_diff=" "$TMP/score_fail.out"'

# Verify the pytest invocation is scoped to the canonical test file so the
# collection errors from unrelated modules (e.g. test_response_model_validation,
# test_sync_handlers_are_async, which import `app.api.v1.agent_activity` that
# doesn't exist on master) don't trip -k zero_column_cap to exit non-zero.
# Without this scope, the harness reports a scoring-infra-failure (pass_tests=0
# in every run) that has nothing to do with the agent's actual output.
cat > "$TMP/fake_pytest_argv.sh" <<'EOF'
#!/usr/bin/env bash
echo "$*" > "$PYTEST_ARGV_LOG"
exit 0
EOF
chmod +x "$TMP/fake_pytest_argv.sh"

( source "$LIB" 2>/dev/null && \
  PYTEST_CMD="$TMP/fake_pytest_argv.sh" \
  PYTEST_ARGV_LOG="$TMP/pytest.argv" \
  BACKEND_DIR="$TMP/wt/backend" \
  score_golden "$TMP/wt" > /dev/null )
check "pytest invocation is scoped to tests/test_kanban_dispatch.py" \
    'grep -q "tests/test_kanban_dispatch.py" "$TMP/pytest.argv"'
check "pytest invocation still uses -k zero_column_cap" \
    'grep -q "\-k zero_column_cap" "$TMP/pytest.argv"'

# ----------------------------------------------------------------------------
echo "Task 4: resolve_measurement_base_ref + prepare_golden_revert"

REPO="$TMP/repo"
mkdir -p "$REPO"
( cd "$REPO" && git init -q -b master && git config user.email t@t && git config user.name t && echo a > a.txt && git add a.txt && git commit -qm a )

# --- resolve_measurement_base_ref -----------------------------------------
# Local-only repo (no origin remote): should fall back to master.
( source "$LIB" 2>/dev/null && \
  echo "$(resolve_measurement_base_ref "$REPO")" > "$TMP/base-ref.out" )
check "resolve_measurement_base_ref returns master when origin/master is absent" \
    '[ "$(cat "$TMP/base-ref.out")" = "master" ]'

# Add a feature branch to confirm the baseline prefers master over feature HEAD.
( cd "$REPO" && git checkout -qb feature && echo feature > a.txt && git add a.txt && git commit -qm feature )

( source "$LIB" 2>/dev/null && \
  echo "$(resolve_measurement_base_ref "$REPO")" > "$TMP/base-ref2.out" )
check "resolve_measurement_base_ref still prefers master on a feature branch" \
    '[ "$(cat "$TMP/base-ref2.out")" = "master" ]'

# --- prepare_golden_revert -----------------------------------------------
mkdir -p "$TMP/golden/backend/app/kanban"
printf '%s\n' 'return {r.name: r.max_sessions for r in rows if r.max_sessions is not None and r.max_sessions >= 0}' > \
    "$TMP/golden/backend/app/kanban/dispatch.py"

( source "$LIB" 2>/dev/null && prepare_golden_revert "$TMP/golden" )
check "prepare_golden_revert flips the fixed line to broken" \
    'grep -q "r.max_sessions > 0" "$TMP/golden/backend/app/kanban/dispatch.py" && \
     ! grep -q "r.max_sessions >= 0" "$TMP/golden/backend/app/kanban/dispatch.py"'

# Idempotency / second call: the broken line is now the only one — calling
# prepare_golden_revert again should be a no-op (still leaves broken > 0).
( source "$LIB" 2>/dev/null && prepare_golden_revert "$TMP/golden" )
check "prepare_golden_revert is idempotent on already-broken state" \
    'grep -q "r.max_sessions > 0" "$TMP/golden/backend/app/kanban/dispatch.py" && \
     ! grep -q "r.max_sessions >= 0" "$TMP/golden/backend/app/kanban/dispatch.py"'

# Fail-closed: a worktree whose dispatch.py lacks the fixed line must refuse.
printf '%s\n' 'return feature-content' > "$TMP/golden/backend/app/kanban/dispatch.py"
if ( source "$LIB" 2>/dev/null && prepare_golden_revert "$TMP/golden" ) >"$TMP/noop.out" 2>"$TMP/noop.err"; then
    NOOP_RC=0
else
    NOOP_RC=$?
fi
check "prepare_golden_revert exits non-zero when fixed line is missing" '[ "$NOOP_RC" -ne 0 ]'
check "prepare_golden_revert explains the invalid baseline" \
    'grep -q "expected fixed line" "$TMP/noop.err"'

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
    *"github.com/JuliusBrussee/caveman"*) variant=card-injector ;;
    *"Host card id: measure-golden-task"*) variant=card-baseline ;;
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

# Every compare run must execute inside the structural sandbox, not a linked
# git worktree inside $REPO_ROOT. The card-shaped variants already require
# this (their prompt carries the real ship recipe); baseline/with-saver
# don't today, but the prompt that drives them is a property the operator
# can edit, and a prompt edit is the same class of regression that pushed
# to origin/master in 2026-08-10 (kaart ee905064…/5934b954…, revert
# 2e0eb256). The stub's $PWD is the run's working tree — assert no .git
# and no reachable git toplevel in any of them.
#
# ROUTING FIX (kaart ee905064…, reviewer-gate round 2): the prior wrapper
# printed violations to STDOUT (which it then redirected to /dev/null) and
# branched pass/fail on the stderr file size — which stayed empty, so the
# check reported `ok` on both healthy AND broken run-trees. New contract:
# violations go to STDERR (file=sys.stderr), bash branches on the python's
# exit code (the source of truth), and the violation list is surfaced in
# the `bad` row's diagnostic text.
#
# Two helpers, one primitive: `_check_sandbox_python` returns the python's
# exit code AND writes stderr to a tmpfile. `assert_sandbox_invariants`
# wraps it and emits a pass/fail row for `check` invocations. The negative
# control (Task 5b) prefers to call `_check_sandbox_python` directly so the
# broken input can be verified without firing a `bad()` row — `bad()` rows
# are detection signals for real failures, and a working negative control
# is the EXPECTED state of the test suite, not a failure.
_check_sandbox_python() {
    local log="$1" repo="$2" home="$3" err_file="$4"
    local rc=0
    python3 - "$log" "$repo" "$home" >/dev/null 2>"$err_file" <<'PY' || rc=$?
import os, subprocess, sys
log, repo, home = sys.argv[1], sys.argv[2], sys.argv[3]
trees = []
with open(log) as fh:
    for line in fh:
        pwd, _, _ = line.rstrip("\n").partition("|")
        if pwd and pwd not in trees:
            trees.append(pwd)
errors = []
for t in trees:
    if os.path.exists(os.path.join(t, ".git")):
        errors.append(f"{t}: contains .git")
    try:
        r = subprocess.run(
            ["git", "-C", t, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            errors.append(f"{t}: resolves as git toplevel ({r.stdout.strip()})")
    except Exception as exc:
        errors.append(f"{t}: git probe failed ({exc})")
    if t.startswith(repo + "/") or t == repo:
        errors.append(f"{t}: lives inside repo root (must be a sandbox)")
    if not t.startswith(home + "/.cache/"):
        errors.append(f"{t}: not under $HOME/.cache (must be the canonical sandbox root)")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PY
    return "$rc"
}

assert_sandbox_invariants() {
    local log="$1" repo="$2" home="$3" label="${4:-compare run-tree sandbox invariants}"
    local rc=0 err_file="$TMP/sandbox_check.$$.err"
    _check_sandbox_python "$log" "$repo" "$home" "$err_file" || rc=$?
    if [ "$rc" -ne 0 ]; then
        local msgs
        msgs="$(tr '\n' ';' < "$err_file")"
        bad "${label} violated (see $(basename "$err_file")): ${msgs}"
        EXPECTED_BAD_AT_END=$((EXPECTED_BAD_AT_END + 1))
    else
        ok "every run-tree has no .git entry, is not a git toplevel, lives under \$HOME/.cache outside the repo root"
    fi
    return "$rc"
}

# The Task 5 healthy-state check now runs through the same wrapper. The
# compare smoke produced exactly the four sandbox-rooted trees we expect,
# so this stays in lockstep with the negative control in the new Task 5b.
check "compare smoke: positive control still passes (healthy run-trees)" \
    'assert_sandbox_invariants "$TMP/claude.log" "$REPO_ROOT_FOR_TESTS" "$HOME" "compare run-tree sandbox invariants"'

# ----------------------------------------------------------------------------
echo "Task 5b: SANDBOX_CHECK catches a broken (in-repo) run-tree log"

# Negative-control regression guard (kaart ee905064… reviewer-gate, two
# rounds). Same wrapper, hand-crafted broken log: the python MUST exit 1
# AND the wrapper MUST report `bad`, not `ok`. The shape of this fixture
# is the smoking-gun class CLAUDE.md # Test-blok names — an assertion that
# passes in both broken and fixed states — except here the assertion is
# designed to FAIL on the prior (broken) wrapper so the regression cannot
# sneak back in via a future refactor.
mkdir -p "$TMP/break/wt/.git"
git -C "$TMP/break/wt" init -q -b master >/dev/null 2>&1 || true
printf '%s|baseline|placeholder\n' "$TMP/break/wt" > "$TMP/break-claude.log"

PRE_FAIL=$FAIL
SANDBOX_NEG_RC=0
# Direct primitive call instead of assert_sandbox_invariants: a working
# negative control is the EXPECTED state of this test, not a failure.
# Routing through assert_sandbox_invariants would fire a `bad()` row
# whose `FAIL:` line `scripts/compare-bash-tests.sh` would mis-attribute
# as a NEW (your fault) failure (kaart ee905064… reviewer-gate, two
# rounds). The asserts below ARE the regression guard — they verify the
# same wrapper-shaped detection without the bad() side-effect.
NEG_ERR="$TMP/sandbox_check.neg.$$.err"
_check_sandbox_python "$TMP/break-claude.log" "$REPO_ROOT_FOR_TESTS" "$HOME" "$NEG_ERR" \
    || SANDBOX_NEG_RC=$?

check "negative control: python exits 1 on a broken run-tree log" \
    '[ "$SANDBOX_NEG_RC" -ne 0 ]'
check "negative control: violation list lands on stderr, not stdout (route fix)" \
    '[ -s "$NEG_ERR" ] && grep -qF "contains .git" "$NEG_ERR"'
check "negative control: violation list names the in-tree-git-toplevel failure" \
    'grep -qF "resolves as git toplevel" "$NEG_ERR"'
check "negative control: violation list names the not-under-HOME/.cache failure" \
    'grep -qE "under .HOME/\.cache" "$NEG_ERR"'

# --- injector-compare smoke (kaart 5934b954…) ---------------------------
# Same stub claude + same fake pytest; this time the harness runs the
# canonical two-arm, two-trial injector-compare. Both arms come out of the
# production build_card_prompt, so the stub distinguishes them by the
# Caveman attribution header (injector arm) versus the card scaffolding
# alone (baseline arm). Order is counterbalanced: trial 1 baseline-first,
# trial 2 injector-first.
MEASURE_CLAUDE_LOG="$TMP/full-claude.log" \
PYTEST_CMD="$TMP/bin/fake-pytest" \
PATH="$TMP/bin:$PATH" \
bash "$SCRIPT_DIR/measure-token-saver.sh" injector-compare > "$TMP/full-compare.out" 2> "$TMP/full-compare.err"

CLAUDE_RUNS="$(wc -l < "$TMP/full-claude.log")"
check "injector-compare invokes exactly four Claude runs (2 arms × 2 trials)" \
    '[ "$CLAUDE_RUNS" -eq 4 ]'
check "injector-compare trial 1 starts with card-baseline" \
    '[ "$(sed -n 1p "$TMP/full-claude.log" | cut -d"|" -f2)" = card-baseline ]'
check "injector-compare trial 1 second is card-injector" \
    '[ "$(sed -n 2p "$TMP/full-claude.log" | cut -d"|" -f2)" = card-injector ]'
check "injector-compare trial 2 reverses the arm order" \
    '[ "$(sed -n 3p "$TMP/full-claude.log" | cut -d"|" -f2)" = card-injector ] && [ "$(sed -n 4p "$TMP/full-claude.log" | cut -d"|" -f2)" = card-baseline ]'
check "injector-compare emits both arms for both trials in the table" \
    '[ "$(grep -c "| trial-[12]-card-injector " "$TMP/full-compare.out")" -eq 2 ] && [ "$(grep -c "| trial-[12]-card-baseline " "$TMP/full-compare.out")" -eq 2 ]'
check "injector-compare emits a delta row per trial" \
    '[ "$(grep -c "| trial-[12]-delta " "$TMP/full-compare.out")" -eq 2 ]'

# --- single-trial card-injector smoke -----------------------------------
MEASURE_CLAUDE_LOG="$TMP/injector-only.log" \
PYTEST_CMD="$TMP/bin/fake-pytest" \
PATH="$TMP/bin:$PATH" \
bash "$SCRIPT_DIR/measure-token-saver.sh" card-injector > "$TMP/injector-only.out" 2> "$TMP/injector-only.err"

check "card-injector subcommand invokes exactly one Claude run" \
    '[ "$(wc -l < "$TMP/injector-only.log")" -eq 1 ]'
check "card-injector single run is detected as the verbatim-slice variant" \
    '[ "$(sed -n 1p "$TMP/injector-only.log" | cut -d"|" -f2)" = card-injector ]'
check "card-injector output table contains the trial-1-card-injector row" \
    'grep -q "| trial-1-card-injector " "$TMP/injector-only.out"'

# ----------------------------------------------------------------------------
# --- a timed-out run is reported as no-measurement -----------------------
# A run killed by `timeout` stops its token counters wherever the kill
# landed. Publishing those next to a run that finished on its own compares a
# partial transcript with a complete one — which is how the first
# injector-compare attempt nearly produced a table where the baseline arm had
# run to completion in 78s and the injector arm had been killed at 300s.
# The harness must withhold usage AND score for such a run and say why.
cat > "$TMP/bin/claude-slow" <<'EOF'
#!/usr/bin/env bash
set -u
if [ "${1:-}" = "--version" ]; then echo "claude-stub 0"; exit 0; fi
cat > /dev/null
sleep 30
EOF
chmod +x "$TMP/bin/claude-slow"
mkdir -p "$TMP/bin-slow"
cp "$TMP/bin/claude-slow" "$TMP/bin-slow/claude"
cp "$TMP/bin/fake-pytest" "$TMP/bin-slow/fake-pytest"
TIMEOUT_DIR="$TMP/timeout-run"
MEASURE_RESULT_DIR="$TIMEOUT_DIR" \
MEASURE_TIMEOUT_S=1 \
MEASURE_CLAUDE_LOG="$TMP/timeout.log" \
PYTEST_CMD="$TMP/bin/fake-pytest" \
PATH="$TMP/bin-slow:$PATH" \
bash "$SCRIPT_DIR/measure-token-saver.sh" card-baseline > "$TMP/timeout.out" 2> "$TMP/timeout.err"

check "timed-out run records exit code 124" \
    '[ "$(cat "$TIMEOUT_DIR/trial-1-card-baseline.exit" 2>/dev/null)" = "124" ]'
check "timed-out run writes no usage file (numbers withheld)" \
    '[ ! -s "$TIMEOUT_DIR/trial-1-card-baseline.usage" ]'
check "timed-out run writes no score file (quality withheld)" \
    '[ ! -s "$TIMEOUT_DIR/trial-1-card-baseline.score" ]'
check "timed-out run explains itself in a .missing marker" \
    'grep -q "hit the 1s timeout" "$TIMEOUT_DIR/trial-1-card-baseline.missing"'
check "timed-out row renders as ? instead of a plausible data point" \
    'grep -qE "\| trial-1-card-baseline .*\|[[:space:]]+\?[[:space:]]+\|" "$TMP/timeout.out"'
check "timed-out row prints the reason under the table row" \
    'grep -q "(reason)" "$TMP/timeout.out"'

# --- card-shaped runs are sandboxed, not worktree'd ----------------------
# A card-shaped prompt carries the real ship recipe. One measured agent
# followed it to `git push origin HEAD:master` on the shared repo, so these
# runs must execute in a tree with no .git, no remote, and no reachable
# parent repository. Env-level transport blocking (GIT_SSH_COMMAND) was tried
# and did not hold — assert the structural property instead.
SANDBOX_TEST="$HOME/.cache/cockpit-measure-sandbox/harness-selftest-$$"
( source "$LIB" && cleanup_prompt_sandbox "$SANDBOX_TEST" ) 2>/dev/null || true
sandbox_rc=0
( source "$LIB" && make_prompt_sandbox "$SCRIPT_DIR/.." HEAD "$SANDBOX_TEST" ) || sandbox_rc=$?
check "make_prompt_sandbox exports the tree" \
    '[ "$sandbox_rc" -eq 0 ] && [ -f "$SANDBOX_TEST/backend/app/kanban/dispatch.py" ]'
check "sandbox contains no .git entry" '[ ! -e "$SANDBOX_TEST/.git" ]'
check "sandbox has no reachable git repository (nothing to push to)" \
    '! ( cd "$SANDBOX_TEST" && git rev-parse --show-toplevel >/dev/null 2>&1 )'
check "cleanup_prompt_sandbox refuses a path outside \$HOME/.cache" \
    '! ( source "$LIB" && cleanup_prompt_sandbox "$TMP/not-a-sandbox" ) 2>/dev/null'
check "cleanup_prompt_sandbox removes its own sandbox" \
    '( source "$LIB" && cleanup_prompt_sandbox "$SANDBOX_TEST" ) && [ ! -d "$SANDBOX_TEST" ]'

echo "Summary: $PASS passed, $FAIL failed (of which $EXPECTED_BAD_AT_END is the negative-control bad() row that proves the regression guard; the exit gate subtracts it via $((FAIL - EXPECTED_BAD_AT_END)) so a working negative-control does not falsely fail the suite)"
[ "$((FAIL - EXPECTED_BAD_AT_END))" -eq 0 ]
