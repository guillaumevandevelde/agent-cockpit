#!/usr/bin/env bash
# Test harness for scripts/check-ci-health.sh.
#
# The SUT (script under test) shells out to `gh` to read recent CI runs and
# the per-run jobs breakdown. We do NOT want to depend on a live `gh` auth
# session from the test harness, and we don't want the test to be flaky when
# the real board happens to be red or green. The SUT therefore accepts a
# fixture directory via `CI_HEALTH_FIXTURES_DIR=<dir>`; in that mode it reads
#   run-list.json                — output of `gh run list --json databaseId,conclusion,headBranch,workflowDatabaseId,name`
#   run-<id>.json                — output of `gh run view --json jobs <id>`
# instead of calling `gh api`. The harness builds those fixtures synthetically.
#
# Tasks covered (mirroring the card's acceptance criteria):
#   1.  arg parsing — `--help` works and mentions the real flags.
#   2.  error — missing fixtures dir → exit 2.
#   3.  error — unknown argument → exit 2.
#   4.  empty fixtures dir → exit 0 with "no runs" OK line.
#   5.  **CI-didn't-run check**: a recent run with `conclusion=failure` and
#       zero steps in any job (the billing-block signature) → flagged with a
#       distinct "infrastructure" message (acceptance criterion 1).
#   6.  **CI-didn't-run check**: the same scenario but EVERY job has zero
#       steps and the run total runtime < 10s → also flagged.
#   7.  **Real test failure**: a run with `conclusion=failure` but a normal
#       step count + normal duration → NOT flagged as "infrastructure",
#       just contributes to the consecutive-red count.
#   8.  **consecutive-red**: last N (default 3) completed runs on master are
#       all failure → flagged (acceptance criterion 2). Configurable via
#       `--red-threshold=N`.
#   9.  consecutive-red threshold: with threshold=5 and 3 reds → not flagged.
#  10.  consecutive-red respects branch: a failure on a non-master branch
#       does NOT count toward the master streak.
#  11.  the threshold default is 3 — checked by arg-parsing test.
#  12.  exit-1 only under --strict (matching sibling check-*.sh scripts).
#  13.  the same fixture set produces BOTH warnings → both lines emitted;
#       one --strict run covers both failure modes.
#  14.  **live-order regression**: fixtures mirror real `gh run list`
#       (newest at JSON index 0). The original bug had the loop walking
#       OLDEST-FIRST and breaking out on older non-master failures before
#       ever reaching a fresh billing-block at the top. This task passes
#       args in newest-first order — i.e. the literal shape `gh run list`
#       produces — and asserts both warnings fire.
#  15.  live-order + --strict exits 1 (regression for the silent-clean bug).
#  16.  live-order with newest GREEN, older 3 reds → no streak (newest
#       green is a streak boundary; the SUT's semantics ask "did the
#       recent pushes break?", not "did we ever break historically?").
#  17.  live-order regression: 3 master reds at the top, with older
#       dependabot/green noise below them. The buggy oldest-first loop
#       would have walked the bottom entries first and broken out
#       before reaching the reds — silently reporting healthy while CI
#       was structurally red.
#  18.  live-order edge: green-then-billing-block. Infra warning fires;
#       no consecutive-red (only 1 failure, on top of a green boundary).
#  19.  newest non-master run does not hide billing-blocked master runs below
#       it, whether the PR run itself is green or billing-blocked.
#  20.  cockpit-doctor check #9 surfaces reproduction B as a CI-health WARN
#       and never prints the misleading "CI health clean" PASS.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-ci-health.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
# `check <description> <expression>` — runs the expression in a fresh
# subshell with the captured stdout/stderr passed via the env var `$OUT`.
# Using an env var (rather than `eval "echo \"$out\" | ..."`) is
# load-bearing: the SUT's --help text contains backticks (`` ``conclusion
# == failure`` ``), and `eval "echo \"$out\" | ..."` would evaluate those
# backticks as command substitution, hiding the actual line and producing
# false negatives. The env-var handoff keeps the data out of the shell
# parser on the receiving side — backticks in `$OUT` stay literal text.
check(){
  local desc="$1" expr="$2"
  # Pass captured stdout+stderr as $OUT and the captured exit code as $RC
  # via env vars (NOT positional args), so the expression's `$OUT` /
  # `$RC` references resolve inside the fresh subshell. Env-var handoff is
  # load-bearing: the SUT's --help text contains backticks, and any
  # `eval`-based interpolation would evaluate them as command substitution.
  # A separate env var for $RC means expressions like `[ "$RC" -eq 0 ]`
  # work the same way the existing check-*.sh test harnesses use them.
  # `: "${rc:=}"` guards against `set -u` on the first --help call where
  # `out=...; rc=$?` hasn't run yet (no $rc is in scope).
  : "${rc:=}"
  if OUT="$out" RC="$rc" bash -c "$expr"; then ok "$desc"; else bad "$desc"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Helper: build a `run-list.json` and per-run `run-<id>.json` fixtures in
# `$1` from a simple `runs` spec: each entry is `id|conclusion|branch|dur_s`.
# The run-id is the same as the databaseId; jobs are derived from the duration
# (long → 14-step backend + 10-step frontend + 0-step skipped e2e; short/zero
# → all jobs empty-step). The shape is intentionally close enough to the real
# `gh run view --json jobs` output for the SUT's jq-free awk parser.
write_fixtures() {
  local dir="$1"; shift
  mkdir -p "$dir"
  : > "$dir/run-list.json"
  printf '[' >> "$dir/run-list.json"
  local first=1
  for spec in "$@"; do
    IFS='|' read -r id conc branch dur <<<"$spec"
    if [ "$first" -eq 0 ]; then printf ',' >> "$dir/run-list.json"; fi
    first=0
    cat >> "$dir/run-list.json" <<EOF
{"databaseId":${id},"conclusion":"${conc}","headBranch":"${branch}","workflowDatabaseId":1,"name":"Quality"}
EOF
    # Per-run JSON. Long-duration → 14-step backend failure + 10-step frontend
    # success + 0-step skipped e2e (mirrors the real run observed on master
    # 2026-07-28). Short-duration → every job is empty-step + conclusion
    # failure (the billing-block signature).
    if [ "${dur:-0}" -ge 30 ]; then
      cat > "$dir/run-${id}.json" <<EOF
{"jobs":[
{"name":"backend","conclusion":"${conc}","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:${dur#0}Z","steps":[
{"name":"Set up job","conclusion":"${conc}","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:01Z","number":1},
{"name":"Run actions/checkout@v7","conclusion":"${conc}","startedAt":"2026-07-28T10:00:01Z","completedAt":"2026-07-28T10:00:02Z","number":2},
{"name":"Run ruff","conclusion":"${conc}","startedAt":"2026-07-28T10:00:02Z","completedAt":"2026-07-28T10:00:10Z","number":3},
{"name":"Run bandit","conclusion":"${conc}","startedAt":"2026-07-28T10:00:10Z","completedAt":"2026-07-28T10:00:18Z","number":4},
{"name":"Run pytest","conclusion":"${conc}","startedAt":"2026-07-28T10:00:18Z","completedAt":"2026-07-28T10:00:40Z","number":5},
{"name":"Complete job","conclusion":"${conc}","startedAt":"2026-07-28T10:00:40Z","completedAt":"2026-07-28T10:00:46Z","number":6}
]},
{"name":"frontend","conclusion":"success","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:01:20Z","steps":[
{"name":"Set up job","conclusion":"success","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:01Z","number":1},
{"name":"Complete job","conclusion":"success","startedAt":"2026-07-28T10:01:20Z","completedAt":"2026-07-28T10:01:20Z","number":15}
]},
{"name":"e2e","conclusion":"skipped","startedAt":"2026-07-28T10:01:20Z","completedAt":"2026-07-28T10:01:20Z","steps":[]}
]}
EOF
    else
      # Short or zero duration → empty-step + failure on every job (the
      # billing-block signature). Dur is intentionally small so the
      # run-level "<10s" rule fires for tasks 5/6.
      cat > "$dir/run-${id}.json" <<EOF
{"jobs":[
{"name":"backend","conclusion":"${conc}","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:0${dur:-0}Z","steps":[]},
{"name":"frontend","conclusion":"${conc}","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:0${dur:-0}Z","steps":[]},
{"name":"e2e","conclusion":"${conc}","startedAt":"2026-07-28T10:00:00Z","completedAt":"2026-07-28T10:00:0${dur:-0}Z","steps":[]}
]}
EOF
    fi
  done
  printf ']' >> "$dir/run-list.json"
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage" 'echo "$OUT" | grep -qE "Usage:"'
check "--help mentions --strict" 'echo "$OUT" | grep -qE "\-\-strict"'
check "--help mentions --red-threshold" 'echo "$OUT" | grep -qE "\-\-red-threshold"'
check "--help mentions --fixtures-dir" 'echo "$OUT" | grep -qE "\-\-fixtures-dir"'
check "--help mentions the empty-steps signature" 'echo "$OUT" | grep -qiE "steps"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing fixtures dir"
out=$(CI_HEALTH_FIXTURES_DIR="$TMP/nope" bash "$SUT" 2>&1); rc=$?
check "missing fixtures dir → exit 2" '[ "$RC" -eq 2 ]'
check "missing fixtures dir → ERROR mentions path" 'echo "$OUT" | grep -qE "ERROR.*fixtures"'

# ----------------------------------------------------------------------------
echo "Task 3: error — unknown argument"
out=$(bash "$SUT" --bogus 2>&1); rc=$?
check "unknown argument → exit 2" '[ "$RC" -eq 2 ]'
check "unknown argument → ERROR names the flag" 'echo "$OUT" | grep -qE "ERROR.*--bogus"'

# ----------------------------------------------------------------------------
echo "Task 4: empty fixtures dir → exit 0 with a clean OK"
empty="$TMP/empty"; mkdir -p "$empty"
: > "$empty/run-list.json"
printf '[]\n' > "$empty/run-list.json"
out=$(CI_HEALTH_FIXTURES_DIR="$empty" bash "$SUT" 2>&1); rc=$?
check "empty fixtures → exit 0" '[ "$RC" -eq 0 ]'
check "empty fixtures → emits an OK line" 'echo "$OUT" | grep -qE "^OK:"'
check "empty fixtures → no WARNING line" '! echo "$OUT" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 5: billing-block signature — conclusion=failure with empty-step jobs and short run duration"
# Run 101 is the most recent (largest id). Steps are empty, duration is 3s.
# The SUT must flag this with a distinct "infrastructure" message.
bb="$TMP/bb"
write_fixtures "$bb" \
  "101|failure|master|3"
out=$(CI_HEALTH_FIXTURES_DIR="$bb" bash "$SUT" 2>&1); rc=$?
check "billing-block → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "billing-block → emits WARNING" 'echo "$OUT" | grep -qE "WARNING:"'
check "billing-block → message mentions infrastructure OR billing OR empty-steps" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "billing-block → names the run id" 'echo "$OUT" | grep -qE "#?101"'
check "billing-block → does NOT say consecutive-red" '! echo "$OUT" | grep -qiE "consecutive"'

# ----------------------------------------------------------------------------
echo "Task 6: billing-block signature — every job empty-steps AND total duration <10s"
# Same shape but with duration 0 — exercises the alternate "looptijd < 10s" branch
# of the rule (kanban card text: "of looptijd < ~10s").
bb2="$TMP/bb2"
write_fixtures "$bb2" \
  "201|failure|master|0"
out=$(CI_HEALTH_FIXTURES_DIR="$bb2" bash "$SUT" 2>&1); rc=$?
check "bb2 → exit 0" '[ "$RC" -eq 0 ]'
check "bb2 → flagged as infrastructure" 'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'

# ----------------------------------------------------------------------------
echo "Task 7: real test failure — many steps + normal duration → not the infrastructure path"
# This is the "your tests are red, not CI" case. It should NOT trigger the
# empty-steps/billing warning; it may contribute to the consecutive-red
# counter (covered separately below).
real="$TMP/real"
write_fixtures "$real" \
  "301|failure|master|46"
out=$(CI_HEALTH_FIXTURES_DIR="$real" bash "$SUT" 2>&1); rc=$?
check "real-fail → exit 0 (advisory under default threshold)" '[ "$RC" -eq 0 ]'
check "real-fail → does NOT mention infrastructure/billing/empty-steps" \
  '! echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'

# ----------------------------------------------------------------------------
echo "Task 8: consecutive-red — last 3 runs on master all fail → flagged"
cr="$TMP/cr"
write_fixtures "$cr" \
  "401|failure|master|46" \
  "402|failure|master|55" \
  "403|failure|master|40"
out=$(CI_HEALTH_FIXTURES_DIR="$cr" bash "$SUT" 2>&1); rc=$?
check "3-red → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "3-red → mentions consecutive" 'echo "$OUT" | grep -qiE "consecutive|streak|in a row"'
check "3-red → names the threshold (3)" 'echo "$OUT" | grep -qE "3"'

# ----------------------------------------------------------------------------
echo "Task 9: --red-threshold=N raises the bar — 3 reds but threshold=5 → not flagged"
out=$(CI_HEALTH_FIXTURES_DIR="$cr" bash "$SUT" --red-threshold=5 2>&1); rc=$?
check "3-red / threshold=5 → exit 0" '[ "$RC" -eq 0 ]'
check "3-red / threshold=5 → does NOT flag consecutive-red" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 10: only master counts — a non-master failure does not extend the streak"
mixed="$TMP/mixed"
write_fixtures "$mixed" \
  "501|failure|master|46" \
  "502|failure|master|55" \
  "503|failure|feature/x|40" \
  "504|failure|master|50"
# Runs are sorted newest-first by id; we want the script to walk from the
# most recent. Run 504 is newest; 503 (non-master) breaks the master streak.
out=$(CI_HEALTH_FIXTURES_DIR="$mixed" bash "$SUT" 2>&1); rc=$?
check "mixed-branches → exit 0" '[ "$RC" -eq 0 ]'
# With threshold=3 the consecutive-master-red streak is at most 1 (run 504
# alone, then 503 breaks it). So no consecutive warning.
check "mixed-branches → no consecutive-red warning" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 11: --strict exits 1 on the consecutive-red warning"
out=$(CI_HEALTH_FIXTURES_DIR="$cr" bash "$SUT" --strict 2>&1); rc=$?
check "3-red --strict → exit 1" '[ "$RC" -eq 1 ]'
check "3-red --strict → still names the threshold" 'echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 12: --strict exits 1 on the billing-block warning"
out=$(CI_HEALTH_FIXTURES_DIR="$bb" bash "$SUT" --strict 2>&1); rc=$?
check "billing-block --strict → exit 1" '[ "$RC" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 13: combined — both warnings fire on one run-set"
both="$TMP/both"
# Most-recent run (601) is a billing-block signature; the previous two (602,
# 603) are real test failures. We expect: 601 → infrastructure warning,
# AND the last 3 master runs (601, 602, 603) are all failure → consecutive-red
# warning. Two warnings from one fixture set.
write_fixtures "$both" \
  "601|failure|master|3" \
  "602|failure|master|46" \
  "603|failure|master|55"
out=$(CI_HEALTH_FIXTURES_DIR="$both" bash "$SUT" 2>&1); rc=$?
check "combined → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "combined → mentions infrastructure" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "combined → mentions consecutive-red" \
  'echo "$OUT" | grep -qiE "consecutive|streak|in a row"'
out=$(CI_HEALTH_FIXTURES_DIR="$both" bash "$SUT" --strict 2>&1); rc=$?
check "combined --strict → exit 1" '[ "$RC" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 14: live-order regression — fixtures mirror real 'gh run list'"
# Real `gh run list` returns runs NEWEST-FIRST (the most recent run is
# index 0 in the JSON array). The original loop walked OLDEST-FIRST and
# exited the moment it hit a non-master failure or non-failure in the
# older entries — meaning a fresh billing-block at the top of the list
# was never seen, and N consecutive red master pushes after a long
# stretch of green builds were never accumulated. write_fixtures writes
# args into run-list.json in the order given, so passing them in
# newest-first order here mirrors the real gh output.
#
# Fixture shape (newest at index 0, oldest last):
#   8001 master fail 3s   ← billing-block (newest)
#   8002 master fail 46s  ← real test failure
#   8003 master fail 46s  ← real test failure
#
# Expected under a correctly-walking newest-first loop:
#   - infra warning from 8001 (AC 1)
#   - consecutive-red warning (3 master failures in a row from the top,
#     AC 2)
# With the OLD oldest-first loop, the script walked 8003 → 8002 → 8001
# instead. That happened to accumulate the same streak in this exact
# 3-element shape — so a 3-only fixture isn't enough to surface the
# bug. See task 17 for the case where the buggy loop silently reported
# clean.
live="$TMP/live"
write_fixtures "$live" \
  "8001|failure|master|3" \
  "8002|failure|master|46" \
  "8003|failure|master|46"
out=$(CI_HEALTH_FIXTURES_DIR="$live" bash "$SUT" 2>&1); rc=$?
check "live-order → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "live-order → flags the billing-block at the top (AC 1 regression)" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "live-order → flags the 3 consecutive master reds (AC 2 regression)" \
  'echo "$OUT" | grep -qiE "consecutive|streak|in a row"'
check "live-order → names the newest run id (#8001)" \
  'echo "$OUT" | grep -qE "#?8001"'

# ----------------------------------------------------------------------------
echo "Task 15: live-order with --strict exits 1 when both warnings fire"
out=$(CI_HEALTH_FIXTURES_DIR="$live" bash "$SUT" --strict 2>&1); rc=$?
check "live-order --strict → exit 1 (was silently clean under buggy loop)" \
  '[ "$RC" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 16: live-order — newest run is GREEN, older 3 master reds"
# Inverse trap: the SUT's semantics is "did the recent pushes break?",
# not "did we ever break historically?". Newest green at index 0 is a
# streak boundary; the older 3 reds are no longer the most-recent N.
# Real gh run list puts the newest at index 0; an operator looking at
# "5 minutes ago: green ✓" shouldn't see a "3 consecutive reds" warning
# about pushes from an hour ago.
live2="$TMP/live2"
write_fixtures "$live2" \
  "8101|success|master|46" \
  "8102|failure|master|46" \
  "8103|failure|master|46" \
  "8104|failure|master|46"
out=$(CI_HEALTH_FIXTURES_DIR="$live2" bash "$SUT" 2>&1); rc=$?
check "live2 → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "live2 → no consecutive-red (newest green breaks the streak)" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 17: live-order — 3 master reds ABOVE an older non-master push"
# This is the regression that catches the buggy oldest-first loop.
# Real-world newest-first timeline:
#   8201 master fail 3s   ← billing-block (newest, infra)
#   8202 master fail 46s  ← real test failure
#   8203 master fail 46s  ← real test failure
#   8204 dependabot/foo fail 46s   ← non-master (counts as boundary)
#   8205 master success 46s        ← older green (also boundary)
#
# Newest-first walk (correct):
#   8201 → infra warn, streak=1
#   8202 → streak=2
#   8203 → streak=3 → consecutive warn
#
# Old buggy oldest-first walk would visit 8205 (green) first → break,
# then 8204 (non-master) → break again with streak=0, then never
# accumulate 8201-8203. Reports "healthy" while CI is structurally red.
live3="$TMP/live3"
write_fixtures "$live3" \
  "8201|failure|master|3" \
  "8202|failure|master|46" \
  "8203|failure|master|46" \
  "8204|failure|dependabot/foo|46" \
  "8205|success|master|46"
out=$(CI_HEALTH_FIXTURES_DIR="$live3" bash "$SUT" 2>&1); rc=$?
check "live3 → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "live3 → flags the billing-block at the top (AC 1, even with noise below)" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "live3 → flags 3 consecutive master reds despite dependabot below (AC 2)" \
  'echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 18: live-order — green-then-billing-block, only infra fires"
# Edge case: a green push lands, then minutes later CI hits a billing
# block. The infra warning fires; the streak counter starts at 0 because
# the green is the streak boundary — and we DO NOT want to flag
# consecutive-red (there's only 1 failure on top of a green).
live4="$TMP/live4"
write_fixtures "$live4" \
  "8301|failure|master|3" \
  "8302|success|master|46"
out=$(CI_HEALTH_FIXTURES_DIR="$live4" bash "$SUT" 2>&1); rc=$?
check "live4 → exit 0 (advisory)" '[ "$RC" -eq 0 ]'
check "live4 → flags infra" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "live4 → does NOT flag consecutive-red (only 1 failure on top of green)" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 19: newest PR run does not hide billing-blocked master runs below"
pr_green="$TMP/pr-green"
write_fixtures "$pr_green" \
  "9001|success|k-feature-x|46" \
  "9002|failure|master|3" \
  "9003|failure|master|3" \
  "9004|failure|master|3"
out=$(CI_HEALTH_FIXTURES_DIR="$pr_green" bash "$SUT" 2>&1); rc=$?
check "green PR above billing-blocked master → emits infrastructure warning" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "green PR above billing-blocked master → names first master run (#9002)" \
  'echo "$OUT" | grep -qE "#?9002"'
check "green PR remains a streak boundary" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

pr_blocked="$TMP/pr-blocked"
write_fixtures "$pr_blocked" \
  "9101|failure|k-feature-x|3" \
  "9102|failure|master|3" \
  "9103|failure|master|3" \
  "9104|failure|master|3"
out=$(CI_HEALTH_FIXTURES_DIR="$pr_blocked" bash "$SUT" 2>&1); rc=$?
check "blocked PR above billing-blocked master → emits infrastructure warning" \
  'echo "$OUT" | grep -qiE "infrastructure|billing|empty[- ]steps|did not (run|execute)"'
check "blocked PR above billing-blocked master → names first master run (#9102)" \
  'echo "$OUT" | grep -qE "#?9102"'
check "blocked PR remains a streak boundary" \
  '! echo "$OUT" | grep -qiE "consecutive|streak|in a row"'

# ----------------------------------------------------------------------------
echo "Task 20: cockpit-doctor check #9 does not call reproduction B clean"
GH_BIN="$TMP/fake-gh-bin"
mkdir -p "$GH_BIN"
cat > "$GH_BIN/gh" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = "auth" ] && exit 0
exit 0
EOF
chmod +x "$GH_BIN/gh"
out=$(CI_HEALTH_FIXTURES_DIR="$pr_blocked" PATH="$GH_BIN:$PATH" \
  bash "$SCRIPT_DIR/cockpit-doctor.sh" 2>&1); rc=$?
check "doctor reproduction B → emits CI health warning" \
  'echo "$OUT" | sed -E '\''s/\x1b\[[0-9;]*[a-zA-Z]//g'\'' | grep -qE "WARN.*CI health"'
check "doctor reproduction B → does NOT say CI health clean" \
  '! echo "$OUT" | grep -qE "CI health clean"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]