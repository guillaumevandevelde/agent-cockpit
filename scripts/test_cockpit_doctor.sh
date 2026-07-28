#!/usr/bin/env bash
# Test harness for scripts/cockpit-doctor.sh's CI-health check (#9).
#
# The doctor delegates to scripts/check-ci-health.sh, which can:
#   - exit 0 + WARNING: lines   → CI is genuinely red; warn
#   - exit 0 (no warnings)      → CI is healthy; pass
#   - exit 2                    → SUT itself failed (workflow not
#                                 resolvable, missing prereq, etc.);
#                                 the GATE IS UNVERIFIED. This is the
#                                 scenario that motivated this card:
#                                 the previous `|| true` swallowed the
#                                 exit code and reported a silent PASS,
#                                 exactly the "reassurance worse than
#                                 silence" failure mode.
#
# This harness swaps the real $ROOT/scripts/check-ci-health.sh for a stub
# in a fresh shadow checkout, runs the doctor from there, then cleans up.
# The shadow uses the real repo's .git/ (so `git rev-parse` resolves
# correctly) but a private scripts/ directory we control — that way we
# never mutate the worktree.
#
# Coverage:
#   1.  SUT exit 2 → doctor WARNs with the UNVERIFIED message and the
#       SUT's ERROR line (NOT a silent PASS — the original bug).
#   2.  SUT exit 0 + WARNING → doctor WARNs with the count.
#   3.  SUT exit 0, no WARNING → doctor PASSes "CI health clean".
#   4.  No gh auth (stubbed absent) → doctor PASSes "check skipped".
#   5.  SUT exit 1 + WARNING → doctor still WARNs (--strict exit, but
#       the WARN line count is the operator signal).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCTOR="$REPO_ROOT/scripts/cockpit-doctor.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
# `check <description> <expression>` — runs the expression in a fresh
# subshell with $out / $rc / $clean captured by the test task above.
check(){
  local desc="$1" expr="$2"
  if eval "$expr"; then ok "$desc"; else bad "$desc"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Build the shadow checkout. Symlink .git/ to the real one so `git
# rev-parse --show-toplevel` resolves correctly when the doctor is run
# from inside the shadow; symlink every other file/dir we don't need
# to mutate (frontend/, backend/, docs/, …). Only `scripts/` is a real
# copy so we can install a fake check-ci-health.sh there.
SHADOW="$TMP/shadow"
mkdir -p "$SHADOW"
ln -s "$REPO_ROOT/.git" "$SHADOW/.git"
ln -s "$REPO_ROOT/CLAUDE.md" "$SHADOW/CLAUDE.md" 2>/dev/null || cp "$REPO_ROOT/CLAUDE.md" "$SHADOW/CLAUDE.md" 2>/dev/null || true
cp -R "$REPO_ROOT/scripts" "$SHADOW/scripts"

# Fake `gh` that always reports authenticated. doctor calls `gh auth
# status` to gate the CI check; if it returns non-zero, the check is
# skipped. The doctor has `command -v gh >/dev/null` on PATH and runs
# `gh auth status`. With this fake on PATH (and FAKE_GH_AUTH=ok as the
# default), the gate succeeds and check #9 runs against our stub.
GH_BIN="$TMP/fake-bin"
mkdir -p "$GH_BIN"
cat > "$GH_BIN/gh" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  auth) [ "${FAKE_GH_AUTH:-ok}" = "ok" ] && exit 0 || exit 1 ;;
  *)    exit 0 ;;
esac
EOF
chmod +x "$GH_BIN/gh"

# Install a stub check-ci-health.sh in the shadow. Args:
#   $1 = exit code, $2 = a single line of SUT output (the SUT prints
#        `WARNING: …`, `OK: …`, or `check-ci-health: ERROR: …` lines;
#        the stub writes whatever you pass verbatim so the test can
#        exercise the doctor's grep for `^WARNING:` vs `^check-ci-health:`).
#
# Avoid heredocs entirely for the variable parts — heredoc-with-$VAR
# would mangle `\n` escapes in the printf format and cause the stub to
# silently omit the line, leaving the doctor with "no error text" (the
# exact bug the test is supposed to catch). Build the script via
# printf + redirect to keep `\n` literals intact.
install_stub() {
  local exit_code="$1"; shift
  local line="${1:-}"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'printf '\''check-ci-health  repo=test workflow=quality.yml red-threshold=3\\n'\''\n'
    if [ -n "$line" ]; then
      printf 'printf '\''%%s\\n'\'' '\''%s'\''\n' "$line"
    fi
    printf 'exit %s\n' "$exit_code"
  } > "$SHADOW/scripts/check-ci-health.sh"
  chmod +x "$SHADOW/scripts/check-ci-health.sh"
}

# Run the doctor from inside the shadow so its `cd "\$ROOT"` lands in
# the shadow's scripts/. Capture stdout+stderr, the exit code, and an
# ANSI-stripped copy for grep checks.
run_in_shadow() {
  (
    cd "$SHADOW"
    PATH="$GH_BIN:$PATH" "$DOCTOR"
  )
}

# ----------------------------------------------------------------------------
echo "Task 1: SUT exit 2 (invocation error) → doctor WARNs UNVERIFIED (NOT silent PASS)"
install_stub 2 "check-ci-health: ERROR: workflow 'quality.yml' not found in test/test"
out=$(run_in_shadow); rc=$?
clean=$(printf '%s\n' "$out" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g')
check "doctor exits 0 (warn-only, not fail)" '[ "$rc" -eq 0 ]'
check "doctor emits UNVERIFIED warn"        'echo "$clean" | grep -qE "UNVERIFIED"'
check "doctor surfaces the SUT ERROR line"  'echo "$clean" | grep -qE "workflow.*quality\.yml.*not found"'
check "doctor does NOT say CI health clean" '! echo "$clean" | grep -qE "CI health clean"'

# ----------------------------------------------------------------------------
echo "Task 2: SUT exit 0 + WARNING → doctor WARNs with the count"
install_stub 0 "WARNING: last 3 consecutive quality.yml run(s) on master all concluded failure — \"CI will catch it\" is no longer a safe assumption; investigate before shipping more."
out=$(run_in_shadow); rc=$?
clean=$(printf '%s\n' "$out" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g')
check "doctor exits 0"                       '[ "$rc" -eq 0 ]'
check "doctor warns about CI health WARNING" 'echo "$clean" | grep -qE "CI health: [0-9]+ WARNING"'
check "doctor does NOT say UNVERIFIED"       '! echo "$clean" | grep -qE "UNVERIFIED"'
check "doctor does NOT say CI health clean"  '! echo "$clean" | grep -qE "CI health clean"'

# ----------------------------------------------------------------------------
echo "Task 3: SUT exit 0, no WARNING → doctor PASSes 'CI health clean'"
install_stub 0 ""
out=$(run_in_shadow); rc=$?
clean=$(printf '%s\n' "$out" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g')
check "doctor exits 0"                       '[ "$rc" -eq 0 ]'
check "doctor PASSes 'CI health clean'"      'echo "$clean" | grep -qE "CI health clean"'

# ----------------------------------------------------------------------------
echo "Task 4: No gh auth (stubbed absent) → doctor PASSes 'check skipped'"
install_stub 0 ""
out=$(cd "$SHADOW" && FAKE_GH_AUTH=missing PATH="$GH_BIN:$PATH" "$DOCTOR"); rc=$?
clean=$(printf '%s\n' "$out" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g')
check "doctor exits 0"                            '[ "$rc" -eq 0 ]'
check "doctor PASSes 'check skipped — opt-in'"    'echo "$clean" | grep -qE "check skipped"'
check "doctor does NOT say CI health clean"       '! echo "$clean" | grep -qE "CI health clean"'
check "doctor does NOT say UNVERIFIED"            '! echo "$clean" | grep -qE "UNVERIFIED"'

# ----------------------------------------------------------------------------
echo "Task 5: SUT exit 1 (--strict path) + WARNING → doctor still WARNs"
install_stub 1 "WARNING: last 3 consecutive quality.yml run(s) on master all concluded failure"
out=$(run_in_shadow); rc=$?
clean=$(printf '%s\n' "$out" | sed -E $'s/\x1b\\[[0-9;]*[a-zA-Z]//g')
check "doctor exits 0 (warn-only)"                '[ "$rc" -eq 0 ]'
check "doctor warns about CI health WARNING"      'echo "$clean" | grep -qE "CI health: [0-9]+ WARNING"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
