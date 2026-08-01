#!/usr/bin/env bash
# check-ci-health.sh — flag CI runs that didn't actually run, and CI that's
# been structurally red for N consecutive pushes.
#
# Two checks, run independently, both advisory by default (`--strict` flips
# any hit into exit 1):
#
#   1. **CI didn't actually run** — A *recent* workflow run on `master` with
#      `conclusion == failure` AND (`every job has an empty `steps` array`
#      OR `total run duration is < ~10s`). The classic signature of an
#      Actions billing-block (`recent account payments have failed…`), a
#      runner-capacity outage, or a workflow-syntax error that prevented
#      jobs from being created at all. A red run with normal step counts
#      and 30+ seconds of runtime is a *test* failure and is NOT flagged
#      here — that's a real signal that should land in code review, not a
#      CI-health check. Surfaced with a distinct "infrastructure" message so
#      the operator knows the difference at a glance.
#
#   2. **CI is structurally red** — The last `--red-threshold` completed
#      `quality.yml` runs on `master` are all `conclusion == failure`.
#      Default threshold is 3. Prevents the "CI will catch it" assumption
#      from silently becoming false (the originating card: when Actions
#      billing broke ~2026-07-26, sessions kept shipping while no run
#      actually executed for weeks).
#
# Source of truth: `gh run list --json ...` + `gh run view <id> --json jobs`.
# Tests inject fixtures via `CI_HEALTH_FIXTURES_DIR=<dir>` so the harness
# never depends on a live `gh` session. Fixture shape:
#
#   <dir>/run-list.json    — output of `gh run list --json databaseId,conclusion,headBranch,workflowDatabaseId,name`
#   <dir>/run-<id>.json    — output of `gh run view <id> --json jobs`
#
# When the env var is unset the script calls real `gh`; the `gh` CLI must be
# authenticated. Use `--repo OWNER/REPO` to override the auto-detected repo
# (auto-detection reads `git remote get-url origin`).
#
# Usage:
#   bash scripts/check-ci-health.sh [--strict]
#                                   [--red-threshold=N]      (default 3)
#                                   [--workflow=NAME.yml]    (default quality.yml)
#                                   [--repo=OWNER/REPO]
#                                   [--fixtures-dir=DIR]
#                                   [--limit=N]              (default 20)
#                                   [--help]
#
# Exit codes:
#   0  clean, or any number of hits in advisory mode
#   1  at least one hit AND --strict
#   2  invocation / prereq error (no gh, no fixtures dir, bad flag)

set -uo pipefail

# --- arg parsing -----------------------------------------------------------
STRICT=0
RED_THRESHOLD=3
WORKFLOW="quality.yml"
REPO=""
FIXTURES_DIR=""
LIMIT=20

print_help() {
  sed -n '2,/^set -uo pipefail/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict)         STRICT=1 ;;
    --red-threshold=*) RED_THRESHOLD="${arg#--red-threshold=}" ;;
    --workflow=*)     WORKFLOW="${arg#--workflow=}" ;;
    --repo=*)         REPO="${arg#--repo=}" ;;
    --fixtures-dir=*) FIXTURES_DIR="${arg#--fixtures-dir=}" ;;
    --limit=*)        LIMIT="${arg#--limit=}" ;;
    --help|-h)
      print_help
      exit 0
      ;;
    "")
      ;;
    *)
      echo "check-ci-health: ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$RED_THRESHOLD" =~ ^[0-9]+$ ]] || [ "$RED_THRESHOLD" -lt 1 ]; then
  echo "check-ci-health: ERROR: --red-threshold must be a positive integer (got '$RED_THRESHOLD')" >&2
  exit 2
fi

# --- fixtures vs. live mode ----------------------------------------------
# In fixtures mode the SUT reads pre-baked JSON. In live mode it shells out
# to `gh`. We never mix the two — `FIXTURES_DIR` fully replaces live calls.
LIVE_MODE=1
if [ -n "${CI_HEALTH_FIXTURES_DIR:-}" ]; then
  FIXTURES_DIR="$CI_HEALTH_FIXTURES_DIR"
fi
if [ -n "$FIXTURES_DIR" ]; then
  LIVE_MODE=0
  if [ ! -d "$FIXTURES_DIR" ]; then
    echo "check-ci-health: ERROR: fixtures dir not found: $FIXTURES_DIR" >&2
    echo "Set CI_HEALTH_FIXTURES_DIR or --fixtures-dir to a directory containing run-list.json + run-<id>.json files." >&2
    exit 2
  fi
fi

if [ "$LIVE_MODE" -eq 1 ]; then
  for cmd in gh git; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "check-ci-health: ERROR: required tool '$cmd' not on PATH (or set CI_HEALTH_FIXTURES_DIR)" >&2
      exit 2
    fi
  done
fi

# --- resolve repo ---------------------------------------------------------
if [ -z "$REPO" ]; then
  REPO="$(git remote get-url origin 2>/dev/null \
    | sed -E 's#^git@github.com:##; s#^ssh://git@github.com/##; s#^https://github.com/##; s#\.git$##')"
fi
if [ -z "$REPO" ]; then
  echo "check-ci-health: ERROR: cannot resolve owner/repo from git remote; pass --repo=OWNER/REPO" >&2
  exit 2
fi

bold=$'\033[1m'; red=$'\033[31m'; grn=$'\033[32m'; ylw=$'\033[33m'; rst=$'\033[0m'
worst=0  # 0=ok, 1=warn
# Both markers put the literal `WARNING:` / `OK:` at column 0 — color
# codes wrap the marker text itself (after the colon) so a plain
# `grep -qE "^OK:"` test pattern still matches. This matches the
# convention used by `scripts/cockpit-doctor.sh` PASS/WARN/FAIL markers,
# which sit at column 2 (under their own 2-space indent) and rely on a
# separate `sed` to strip ANSI before counting.
warn() { printf 'WARNING:%s %s\n' "$rst" "$1"; [ "$worst" -lt 1 ] && worst=1; }
pass() { printf 'OK:%s %s\n' "$rst" "$1"; }

printf '%scheck-ci-health%s  repo=%s workflow=%s red-threshold=%s\n' \
  "$bold" "$rst" "$REPO" "$WORKFLOW" "$RED_THRESHOLD"

# --- fetch run list --------------------------------------------------------
# We need the workflow id (not just the file name) so we filter runs to the
# specific workflow. `gh run list --workflow <file>` works on the CLI but
# also returns runs from other workflows when the file-name matches a path
# in multiple repos; the JSON shape with `workflowDatabaseId` is the
# authoritative filter. Resolved via `gh workflow list` once, then compared.
#
# gh workflow list's valid `--json` fields are name, id, path — NOT
# databaseId (that field belongs to `gh run list`'s output, not workflows).
# The human-readable `name` is the workflow's display name (e.g. "Quality"
# for quality.yml); the `path` is the file path. Match on the path's
# basename so callers can pass `--workflow=quality.yml` directly, and use
# python to parse the JSON robustly — the previous awk pattern built a
# regex against compact single-line JSON like `^""quality.yml""` (note the
# doubled quotes), which never matched `{"id":...,"name":"Quality",...}`
# and silently dropped every workflow. That made live mode exit 2 with
# "workflow not found" even when the repo's quality.yml clearly exists.
if [ "$LIVE_MODE" -eq 1 ]; then
  WORKFLOW_LIST_JSON="$(gh workflow list --repo "$REPO" --json name,id,path 2>/dev/null || true)"
  # Surface a real failure when the list call itself dies (network, auth,
  # missing field on an older gh). Bare `|| true` swallowed real errors.
  if [ -z "$WORKFLOW_LIST_JSON" ]; then
    echo "check-ci-health: ERROR: gh workflow list returned no data for $REPO (network/auth?); cannot resolve workflow id." >&2
    exit 2
  fi
  WORKFLOW_ID="$(WORKFLOW_LIST_JSON="$WORKFLOW_LIST_JSON" WORKFLOW="$WORKFLOW" python3 - <<'PY'
import json, os, sys
data = json.loads(os.environ["WORKFLOW_LIST_JSON"])
want = os.environ["WORKFLOW"]
for wf in data:
    path = wf.get("path", "") or ""
    # `path` looks like ".github/workflows/quality.yml"; basename match
    # accepts "quality.yml" (default), ".github/workflows/quality.yml",
    # and the bare "Quality" display name as a convenience fallback.
    base = path.rsplit("/", 1)[-1]
    if base == want or path == want or wf.get("name") == want:
        wid = wf.get("id")
        if wid is not None:
            print(wid)
            sys.exit(0)
sys.exit(1)
PY
)"
  if [ -z "$WORKFLOW_ID" ]; then
    echo "check-ci-health: ERROR: workflow '$WORKFLOW' not found in $REPO" >&2
    exit 2
  fi
  RUN_LIST_JSON="$(gh run list --repo "$REPO" \
    --workflow "$WORKFLOW_ID" \
    --limit "$LIMIT" \
    --json databaseId,conclusion,headBranch,workflowDatabaseId,name 2>/dev/null || true)"
  if [ -z "$RUN_LIST_JSON" ]; then
    echo "check-ci-health: WARNING: gh run list returned no data (network/auth?); nothing to check." >&2
    exit 0
  fi
else
  RUN_LIST_JSON="$(cat "$FIXTURES_DIR/run-list.json")"
fi

# --- helpers ---------------------------------------------------------------
# Fetch the per-run jobs JSON for `id`. In live mode `gh run view` accepts
# a `<id>` positional; in fixture mode we read `run-<id>.json`.
run_view() {
  local id="$1"
  if [ "$LIVE_MODE" -eq 1 ]; then
    gh run view "$id" --repo "$REPO" --json jobs 2>/dev/null || true
  else
    cat "$FIXTURES_DIR/run-${id}.json" 2>/dev/null || true
  fi
}

# Walk one per-run JSON payload and decide whether it counts as the
# "CI didn't actually run" signature. Single-line output:
#   infra|empty=<n>/<n>|dur=<sec>s   — every job empty-step OR run dur <10s, AND at least one failure
#   normal|fail_jobs=<n>|dur=<sec>s  — looks like a real test failure (not infra)
#   no-jobs                          — payload has no jobs at all (nothing to judge)
#   not-fail                         — run-level conclusion isn't failure (skip)
#   parse-error                      — JSON didn't load (shouldn't happen with valid fixtures)
run_signature() {
  local payload="$1"
  python3 - "$payload" <<'PY'
import json, sys
from datetime import datetime
data = json.loads(sys.argv[1])
jobs = data.get("jobs", [])
if not jobs:
    print("no-jobs"); raise SystemExit
empty_jobs = 0
short_jobs = 0
fail_jobs = 0
total_dur = 0
for j in jobs:
    steps = j.get("steps", []) or []
    if not steps:
        empty_jobs += 1
    s = j.get("startedAt"); e = j.get("completedAt")
    if s and e:
        try:
            ds = datetime.fromisoformat(s.replace("Z", "+00:00"))
            de = datetime.fromisoformat(e.replace("Z", "+00:00"))
            d = int((de - ds).total_seconds())
            total_dur = max(total_dur, d)
            if d < 10:
                short_jobs += 1
        except Exception:
            pass
    if j.get("conclusion") == "failure":
        fail_jobs += 1
if fail_jobs == 0:
    print("not-fail"); raise SystemExit
if empty_jobs == len(jobs) or total_dur < 10:
    print(f"infra|empty={empty_jobs}/{len(jobs)}|dur={total_dur}s")
else:
    print(f"normal|fail_jobs={fail_jobs}|dur={total_dur}s")
PY
}

# --- walk runs --------------------------------------------------------------
# The run list from `gh run list` is JSON; we iterate newest-first.
# Use python to parse because we need to iterate ordered records, and that
# requires json.loads anyway (a single awk pass over multi-line JSON is
# fragile). The python helper is the same one we'd write for the test, so
# the harness exercises the real code path.
mapfile -t RUN_IDS < <(python3 - "$RUN_LIST_JSON" "$LIMIT" <<'PY'
import json, sys
runs = json.loads(sys.argv[1])
limit = int(sys.argv[2])
for r in runs[:limit]:
    print(f'{r.get("databaseId","")}|{r.get("conclusion","")}|{r.get("headBranch","")}|{r.get("workflowDatabaseId","")}')
PY
)

if [ "${#RUN_IDS[@]}" -eq 0 ] || [ -z "${RUN_IDS[0]%%|*}" ]; then
  pass "no $WORKFLOW runs found in $REPO (nothing to check)."
  exit 0
fi

master_red_streak=0
streak_open=1
consecutive_red_warned=0

# Walk runs NEWEST-FIRST. `gh run list --limit N` returns JSON with the
# most recent run at index 0; `mapfile` preserves that order so RUN_IDS[0]
# is the newest. Iterate from index 0 forward.
#
# Why this direction matters (the bug that motivated this card): walking
# from the OLDEST run toward the newest — the original direction — would
# break out of the consecutive-red check the moment it hit the first
# older non-master failure or older green build. With real `gh run list`
# output (which interleaves master pushes with feature-branch pushes and
# older green builds), that meant the script silently stopped before
# ever reaching the 3 newest red master runs, and reported "healthy" for
# both AC 1 and AC 2. Walking newest-first sees the operator's actual
# concern — "did the most recent runs break?" — first.
#
# Streak semantics (newest-first walk):
#   - master failure       → streak += 1 (continue)
#   - master non-failure   → streak = 0; close streak check
#   - non-master ANYTHING  → streak = 0; close streak check. A dependabot or
#                            feature-branch run remains a boundary under task
#                            10's contract.
#   - in-flight / queued   → SKIP (empty `conclusion`).
#
# Closing the streak check never ends the loop: check 1 independently scans
# every fetched master/main run for infrastructure failures. The threshold
# check still fires inline before any later boundary can reset the streak.
for ((i=0; i<${#RUN_IDS[@]}; i++)); do
  spec="${RUN_IDS[$i]}"
  IFS='|' read -r rid conc branch wf_id <<<"$spec"
  [ -z "$rid" ] && continue
  # Only consider runs that have actually concluded. In-progress / queued
  # runs have an empty `conclusion`; they don't contribute to either check
  # (an empty conclusion isn't a failure), and skipping them here is what
  # makes the "most recent *completed* run on master" semantics work.
  if [ -z "$conc" ]; then
    continue
  fi

  payload="$(run_view "$rid")"
  [ -z "$payload" ] && continue

  sig="$(run_signature "$payload" 2>/dev/null || echo 'parse-error')"

  # Check 1 — empty-steps / infra signature. Only on master (PR-only runs
  # from feature branches can also show up; flagging those would be noisy
  # since CI-doesn't-run on a feature branch is the engineer's own debug
  # concern, not a board-health one).
  if [ "$branch" = "master" ] || [ "$branch" = "main" ]; then
    case "$sig" in
      infra*)
        warn "CI didn't actually run on $branch run #${rid} (${sig#infra|}) — conclusion=failure with no steps executed; this is an infrastructure/billing signal, NOT a test failure."
        ;;
    esac
  fi

  # Check 2 — consecutive red on master (walk newest→oldest; see
  # semantics table above). A streak boundary closes only this check; check 1
  # must keep scanning older master runs past interleaved PR runs.
  if [ "$streak_open" -eq 1 ]; then
    if [ "$branch" = "master" ] || [ "$branch" = "main" ]; then
      if [ "$conc" = "failure" ]; then
        master_red_streak=$((master_red_streak + 1))
      else
        master_red_streak=0
        streak_open=0
      fi
    else
      # Non-master runs remain a boundary for the master streak (task 10),
      # but not for the independent infrastructure scan above.
      master_red_streak=0
      streak_open=0
    fi

    # Inline threshold check. Fires the moment the streak reaches
    # --red-threshold, regardless of what older runs we still have to
    # scan for infrastructure failures.
    if [ "$master_red_streak" -ge "$RED_THRESHOLD" ] && [ "$consecutive_red_warned" -eq 0 ]; then
      warn "last $master_red_streak consecutive $WORKFLOW run(s) on master all concluded failure — \"CI will catch it\" is no longer a safe assumption; investigate before shipping more."
      consecutive_red_warned=1
    fi
  fi
done

if [ "$worst" -eq 0 ]; then
  pass "$WORKFLOW healthy on $REPO (no runs looked unstarted; last master failure count under threshold $RED_THRESHOLD)."
  exit 0
fi

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — re-run with --strict to enforce)" >&2
exit 0
