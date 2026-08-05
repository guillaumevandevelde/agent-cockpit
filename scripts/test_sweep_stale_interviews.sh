#!/usr/bin/env bash
# Test harness for scripts/sweep_stale_interviews.py.
#
# Exercises the stale-interview sweeper against a synthetic INTERVIEWS_DIR
# fixture, so the tests stay green regardless of what the operator's
# real ~/.claude-registry/interviews/ actually contains. The live-dir
# check is a final optional task — the only real-world invariant the
# sweeper depends on is "is this a directory that looks like a
# scratch-interview artefact?" (vangnet achter kanban-kaart uit
# docs/cockpit/kaartloze-app-inceptie-decision.md §5).
#
# Phases (per .claude/skills/new-app/SKILL.md "state.json"):
#   - interview:        the design+plan dialogue is mid-flight.
#   - ready_for_birth:  interview is finished; birth is the next step.
#   - born:             birth succeeded; the skill promised to move the
#                       scratch dir into .trash/. A scratch dir still
#                       sitting under interviews/ in phase `born` is
#                       the exact "forgot step 6" leftover this sweeper
#                       was built to catch — always flagged.
#
# Sweep rule (mirrors the kanban acceptance criteria):
#   1. Skip dot-prefixed dirs (.trash, .cache, .anything) — they're
#      internal storage, not leftovers.
#   2. For each remaining scratch dir:
#      - phase == "born"     → ALWAYS flagged (the sweeper exists for this).
#      - phase != "born"     → flagged when (age > --older-than-days).
#      - no state.json / parse-error → flagged when (age > --older-than-days)
#        (we can't prove the dir is in flight; same age cut-off applies).
#   3. Advisory by default (exit 0); --strict exits 1 when any hit.
#   4. The script does NOT delete anything — it reports, the human
#      decides (resume vs. delete).
#
# Tasks covered:
#   1.  --help runs and lists all real flags + synopsis.
#   2.  error — missing --interviews-dir → exit 2 + ERROR on stderr.
#   3.  empty interviews dir → exit 0 with empty rows.
#   4.  young interview (phase != born, age < threshold) → not flagged.
#   5.  old interview (phase != born, age > threshold) → flagged.
#   6.  born-phase interview (any age) → ALWAYS flagged.
#   7.  young born-phase interview → flagged (same rule, age-independent).
#   8.  .trash/ subdir is skipped — never reported.
#   9.  dot-prefixed dirs (anything starting with .) are skipped.
#  10.  scratch dir without state.json → treated as old when age > threshold.
#  11.  corrupt state.json → treated as old when age > threshold (parse-error
#       -> unknown phase, fall through to the age check).
#  12.  mixed board — 1 stale + 1 young + 1 born + 1 trash → reports 3.
#  13.  --older-than-days override (1 day lift, 30 day lift).
#  14.  --strict with hits → exit 1; --strict clean → exit 0.
#  15.  row schema: path, age_days, phase, resume_cmd populated.
#  16.  resume_cmd is the literal `/new-app --resume <slug>` for every
#       flagged phase, including `born` (resume verifies the birth and
#       completes the deferred `mv` into `.trash/`).
#  17.  INTERVIEWS_DIR env override works (same precedence as --interviews-dir).
#  18.  real ~/.claude-registry/interviews scans without error.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_stale_interviews.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture helpers. The sweeper uses mtime to compute age, so the helpers
# must backdate mtime via `touch -d` — not "real" sleep, which would
# dominate the test runtime. Birth times live in the script's notion of
# "now" (the same `datetime.now(UTC)` the sweeper samples), so relative
# thresholds are stable.
#
# Args: dir slug phase age_days
make_interview() {
  local dir="$1" slug="$2" phase="$3" age_days="$4"
  mkdir -p "$dir"
  local ts
  ts="$(date -u -d "$age_days days ago" '+%Y-%m-%dT%H:%M:%SZ')"
  cat > "$dir/state.json" <<JSON
{
  "slug": "$slug",
  "project_name": "$slug",
  "phase": "$phase",
  "updated_at": "$ts"
}
JSON
  touch -d "$ts" "$dir"
  touch -d "$ts" "$dir/state.json"
  # IMPORTANT: creating state.json AT step 3 bumps the dir's mtime to
  # ``now`` (writing into a dir updates its mtime). Re-touch the dir
  # AFTER the contents exist so the dir's mtime is consistent with the
  # operator's last edit — otherwise the sweeper's age = 0 verdict
  # suppresses the row. The new-app skill rewrites state.json after
  # every approved section, so the dir's mtime and state.json's mtime
  # move together in real use; the test mirrors that final state.
  touch -d "$ts" "$dir"
}

# Args: dir name  (a dot-prefixed subdir that should be skipped)
make_dot_dir() {
  local dir="$1" name="$2"
  mkdir -p "$dir/$name"
  # Even with ancient mtime, dot-dirs must never be reported.
  touch -d "1970-01-01" "$dir/$name"
}

# Run the SUT pointed at the fixture INTERVIEWS_DIR. Echoes stdout+stderr
# merged; exit code captured by the caller via $?.
run() {
  local idir="$1"; shift
  python3 "$SUT" --interviews-dir "$idir" "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: --help runs and lists all real flags + synopsis"
out=$(python3 "$SUT" --help 2>&1); rc=$?
check "--help runs without error"              '[ "$rc" -eq 0 ]'
check "--help shows synopsis"                  'echo "$out" | grep -qE "^usage:"'
check "--help mentions --interviews-dir"       'echo "$out" | grep -qE "\-\-interviews-dir"'
check "--help mentions --older-than-days"      'echo "$out" | grep -qE "\-\-older-than-days"'
check "--help mentions --strict"               'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --json"                 'echo "$out" | grep -qE "\-\-json"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing --interviews-dir → exit 2"
out=$(python3 "$SUT" --interviews-dir "$TMP/does-not-exist" 2>&1); rc=$?
check "missing dir → exit 2"                  '[ "$rc" -eq 2 ]'
check "missing dir → ERROR on stderr"         'echo "$out" | grep -qE "ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: empty interviews dir → exit 0 with empty rows"
empty="$TMP/empty"
mkdir -p "$empty"
out=$(run "$empty"); rc=$?
check "empty dir → exit 0"                     '[ "$rc" -eq 0 ]'
check "empty dir → valid JSON"                 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "empty dir → 0 rows"                     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "empty dir → interviews_scanned == 0"    'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"interviews_scanned\"]==0, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 4: young interview (phase != born, age < threshold) → not flagged"
young="$TMP/young"
make_interview "$young/young-app" "young-app" "interview" 1
out=$(run "$young"); rc=$?
check "young → exit 0"                         '[ "$rc" -eq 0 ]'
check "young → 0 rows"                         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "young → scanned == 1"                   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"interviews_scanned\"]==1, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: old interview (phase != born, age > threshold) → flagged"
old="$TMP/old"
make_interview "$old/old-app" "old-app" "interview" 30
out=$(run "$old"); rc=$?
check "old interview → exit 0"                 '[ "$rc" -eq 0 ]'
check "old interview → 1 row"                  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "old interview → surfaced by slug"       'echo "$out" | grep -qF "old-app"'
check "old interview → phase 'interview'"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"phase\"]==\"interview\", d[\"rows\"][0]"'
check "old interview → flagged_count == 1"     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"flagged\"]==1, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 6: old ready_for_birth → flagged"
r4b="$TMP/r4b"
make_interview "$r4b/late-birth" "late-birth" "ready_for_birth" 14
out=$(run "$r4b"); rc=$?
check "ready_for_birth old → 1 row"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "ready_for_birth old → phase surfaced"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"phase\"]==\"ready_for_birth\", d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 7: born phase — any age → ALWAYS flagged"
born_young="$TMP/born_young"
make_interview "$born_young/abandoned" "abandoned" "born" 0
out=$(run "$born_young"); rc=$?
check "born young → 1 row"                     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "born young → phase 'born'"              'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"phase\"]==\"born\", d[\"rows\"][0]"'
check "born young → age_days == 0"             'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"age_days\"]==0, d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 8: .trash/ subdir is skipped — never reported"
trash="$TMP/trash"
mkdir -p "$trash/.trash"
# Even .trash/<slug> with phase 'born' must not appear — the sweeper
# ought to treat .trash as its own private store.
mkdir -p "$trash/.trash/old"
cat > "$trash/.trash/old/state.json" <<'JSON'
{"slug": "old", "phase": "born", "updated_at": "1970-01-01T00:00:00Z"}
JSON
touch -d "1970-01-01" "$trash/.trash/old"
touch -d "1970-01-01" "$trash/.trash/old/state.json"
out=$(run "$trash"); rc=$?
check "trash → exit 0"                         '[ "$rc" -eq 0 ]'
check "trash → 0 rows"                         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "trash → scanned == 0"                   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"interviews_scanned\"]==0, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 9: dot-prefixed dirs are skipped"
dots="$TMP/dots"
mkdir -p "$dots/.cache"
touch -d "1970-01-01" "$dots/.cache"
mkdir -p "$dots/.hidden"
touch -d "1970-01-01" "$dots/.hidden"
out=$(run "$dots"); rc=$?
check "dot-dirs → exit 0"                      '[ "$rc" -eq 0 ]'
check "dot-dirs → 0 rows"                      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 10: scratch dir without state.json → flagged when old"
nostate="$TMP/nostate"
mkdir -p "$nostate/orphan"
touch -d "30 days ago" "$nostate/orphan"
out=$(run "$nostate"); rc=$?
check "no state.json + old → 1 row"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "no state.json + old → phase 'unknown'"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"phase\"]==\"unknown\", d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 11: scratch dir without state.json → NOT flagged when young"
nostate_young="$TMP/nostate_young"
mkdir -p "$nostate_young/orphan"
touch -d "1 day ago" "$nostate_young/orphan"
out=$(run "$nostate_young"); rc=$?
check "no state.json + young → 0 rows"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 12: corrupt state.json → treated as old when age > threshold"
corrupt="$TMP/corrupt"
mkdir -p "$corrupt/broken"
echo "not json" > "$corrupt/broken/state.json"
touch -d "30 days ago" "$corrupt/broken"
touch -d "30 days ago" "$corrupt/broken/state.json"
out=$(run "$corrupt"); rc=$?
check "corrupt state + old → 1 row"            'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "corrupt state + old → phase 'unknown'"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"phase\"]==\"unknown\", d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 13: mixed board — stale + young + born + trash → 3 rows"
mixed="$TMP/mixed"
make_interview "$mixed/stale-1" "stale-1" "interview" 30          # flagged (old)
make_interview "$mixed/young-1" "young-1" "interview" 1           # NOT flagged (young)
make_interview "$mixed/old-born" "old-born" "born" 14             # flagged (born)
mkdir -p "$mixed/.trash"
touch -d "1970-01-01" "$mixed/.trash"                             # skipped
out=$(run "$mixed"); rc=$?
check "mixed → exit 0"                          '[ "$rc" -eq 0 ]'
check "mixed → 2 rows"                          'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==2, d[\"rows\"]"'
check "mixed → flagged_count == 2"              'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"flagged\"]==2, d[\"totals\"]"'
check "mixed → scanned == 3"                    'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"interviews_scanned\"]==3, d[\"totals\"]"'
check "mixed → stale-1 surfaced"                'echo "$out" | grep -qF "stale-1"'
check "mixed → old-born surfaced"               'echo "$out" | grep -qF "old-born"'
check "mixed → young-1 absent"                  '! echo "$out" | grep -qF "young-1"'

# ----------------------------------------------------------------------------
echo "Task 14: --older-than-days override (1 day lift, 30 day lift)"
# 14a: a 5-day-old interview is NOT flagged with default 7 days. With
# --older-than-days=1 it IS flagged.
bump="$TMP/bump"
make_interview "$bump/five-day" "five-day" "interview" 5
out=$(run "$bump"); rc=$?
check "5-day default → 0 rows"                  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
out=$(run "$bump" --older-than-days 1); rc=$?
check "5-day --older-than-days=1 → 1 row"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
out=$(run "$bump" --older-than-days 30); rc=$?
check "5-day --older-than-days=30 → 0 rows"     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 15: --strict with hits → exit 1; --strict clean → exit 0"
out=$(run "$old" --strict); rc=$?
check "strict + hits → exit 1"                  '[ "$rc" -eq 1 ]'
out=$(run "$empty" --strict); rc=$?
check "strict + clean → exit 0"                 '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 16: row schema — path, age_days, phase, resume_cmd, reason"
schema="$TMP/schema"
make_interview "$schema/sleeper" "sleeper" "interview" 30
out=$(run "$schema"); rc=$?
check "row has path key"                        'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0].get(\"path\"), d[\"rows\"][0]"'
check "row has age_days key"                    'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert isinstance(d[\"rows\"][0].get(\"age_days\"), int), d[\"rows\"][0]"'
check "row has phase key"                       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0].get(\"phase\")==\"interview\", d[\"rows\"][0]"'
check "row has resume_cmd key"                  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0].get(\"resume_cmd\"), d[\"rows\"][0]"'
check "row has reason key"                      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0].get(\"reason\"), d[\"rows\"][0]"'

# ----------------------------------------------------------------------------
echo "Task 17: resume_cmd shape — every phase uses /new-app --resume"
mkdir -p "$schema/in-flight"
cat > "$schema/in-flight/state.json" <<'JSON'
{"slug": "in-flight", "phase": "interview", "updated_at": "2026-07-01T00:00:00Z"}
JSON
touch -d "30 days ago" "$schema/in-flight"
touch -d "30 days ago" "$schema/in-flight/state.json"
mkdir -p "$schema/at-birth"
cat > "$schema/at-birth/state.json" <<'JSON'
{"slug": "at-birth", "phase": "ready_for_birth", "updated_at": "2026-07-01T00:00:00Z"}
JSON
touch -d "30 days ago" "$schema/at-birth"
touch -d "30 days ago" "$schema/at-birth/state.json"
mkdir -p "$schema/born-resume"
cat > "$schema/born-resume/state.json" <<'JSON'
{"slug": "born-resume", "phase": "born", "updated_at": "2026-07-01T00:00:00Z"}
JSON
touch -d "30 days ago" "$schema/born-resume"
touch -d "30 days ago" "$schema/born-resume/state.json"
out=$(run "$schema"); rc=$?
check "interview → exact resume_cmd"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); rs={r[\"slug\"]:r[\"resume_cmd\"] for r in d[\"rows\"]}; assert rs[\"in-flight\"]==\"/new-app --resume in-flight\", rs"'
check "ready_for_birth → exact resume_cmd" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); rs={r[\"slug\"]:r[\"resume_cmd\"] for r in d[\"rows\"]}; assert rs[\"at-birth\"]==\"/new-app --resume at-birth\", rs"'
check "born → exact resume_cmd" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); rs={r[\"slug\"]:r[\"resume_cmd\"] for r in d[\"rows\"]}; assert rs[\"born-resume\"]==\"/new-app --resume born-resume\", rs"'

# ----------------------------------------------------------------------------
echo "Task 18: INTERVIEWS_DIR env override works"
mkdir -p "$TMP/env-target/env-test"
cat > "$TMP/env-target/env-test/state.json" <<'JSON'
{"slug": "env-test", "phase": "interview", "updated_at": "2026-07-01T00:00:00Z"}
JSON
touch -d "30 days ago" "$TMP/env-target/env-test/state.json"
# ``cat > state.json`` bumps the dir's mtime to now; re-touch the dir
# AFTER the contents exist so the SUT's age = 30 verdict survives.
touch -d "30 days ago" "$TMP/env-target/env-test"
out=$(INTERVIEWS_DIR="$TMP/env-target" python3 "$SUT" 2>&1); rc=$?
check "env override → exit 0"                  '[ "$rc" -eq 0 ]'
check "env override → 1 row"                   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "env override → surfaced env-test"       'echo "$out" | grep -qF "env-test"'

# ----------------------------------------------------------------------------
echo "Task 19: real ~/.claude-registry/interviews scans without error"
if [ -d "$HOME/.claude-registry/interviews" ]; then
  out=$(python3 "$SUT" 2>&1); rc=$?
  check "real dir → exit 0 (advisory)"          '[ "$rc" -eq 0 ]'
  check "real dir → valid JSON"                 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
  check "real dir → no python traceback"        '! echo "$out" | grep -qE "Traceback"'
else
  echo "  (skip — $HOME/.claude-registry/interviews not present)"
fi

# Specific clean-state line. Keep this exact: callers must grep only this OK
# sentence, never an `^OK:|WARNING:` alternation that also accepts a warning.
echo ""
echo "passed: $PASS, failed: $FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "OK: stale interview sweeper fixture contract passed."
else
  echo "FAIL: stale interview sweeper fixture contract has $FAIL failure(s)."
fi
[ "$FAIL" -eq 0 ]
