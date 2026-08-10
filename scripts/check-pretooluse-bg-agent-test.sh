#!/usr/bin/env bash
#
# check-pretooluse-bg-agent-test.sh — CI gate that fires when a `PreToolUse`
# hook is added to `.claude/settings.json` without an accompanying test that
# covers the background-agent invocation path.
#
# Background (kanban card a712f5c65f1545678f57b1f4ab450514): Claude Code
# 2.1.222 (Aug 4, 2026) fixed a permission bypass in which `PreToolUse`
# auto-allow hooks silently bypassed restrictions in background agents
# (subagent, summary, compaction, rename). CC 2.1.226 is now installed on
# this box, so the upstream fix is live — the residual risk is in our
# own test coverage: a hook that looks fine in a foreground test can
# silently drop restrictions on the background route, leaking the exact
# failure class kanban card `513e37a1a86e41db8b6af8423292f6b6` captures
# (a write landing on the wrong checkout without an error).
#
# This gate is a no-op today — `.claude/settings.json` ships with
# `"hooks": { "PreToolUse": [] }` (verified 2026-08-08). The moment the
# first real hook is added without a background-agent test, this script
# fails (under --strict) or warns (advisory default).
#
# Two equivalent ways to satisfy the gate; either is enough:
#
#   1. **Marker file** `.claude/hooks/pretooluse-bg-agent-test-pass`.
#      Touch a file with that exact name at that exact path. Cheap,
#      explicit, and grep-able from the activity feed. The test author
#      creates this file alongside the test it documents. Stability
#      wins over cleverness: a marker file makes the contract bullet-
#      proof and survives pytest renames / test-file splits.
#
#   2. **Test file** under `backend/tests/` whose name contains BOTH
#      `pretooluse` AND `background` (case-insensitive substring match).
#      Mirrors the marker-file intent for crews that prefer the test
#      itself to be the documentation. The exact pattern is
#      `test_pretooluse_*_background*.py` or equivalent.
#
# Either path satisfies the contract. The script does NOT verify the
# test actually exercises a background-agent call — it only verifies the
# *contract* (a hook implies a test). Asserting the test fires for a
# background invocation is the test author's job, and the failure mode
# we guard against is the team that adds a hook and forgets the test
# entirely, not the team that writes a lazy test.
#
# Usage:
#   bash scripts/check-pretooluse-bg-agent-test.sh [--strict]
#   # Defaults to <repo>/.claude/settings.json and <repo>/backend/tests.
#   # Override with SETTINGS_JSON / TESTS_DIR / REPO_ROOT env vars.
#
# Exit codes:
#   0  clean OR (advisory mode and >=1 hit)
#   1  --strict and >=1 hit
#   2  usage error / repo missing / not a git repo

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"
SETTINGS_JSON="${SETTINGS_JSON:-}"
TESTS_DIR="${TESTS_DIR:-}"
MARKER_FILE_REL=".claude/hooks/pretooluse-bg-agent-test-pass"
MARKER_FILE_ABS=""
STRICT=0

usage() {
  sed -n '2,/^set -uo pipefail/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --settings=*) SETTINGS_JSON="${arg#--settings=}" ;;
    --tests-dir=*) TESTS_DIR="${arg#--tests-dir=}" ;;
    --repo=*) REPO_ROOT="${arg#--repo=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "usage error: unknown argument '$arg'" >&2; usage >&2; exit 2 ;;
  esac
done

# Default the derived paths AFTER arg parsing so `--repo=X` actually
# moves SETTINGS_JSON / TESTS_DIR / MARKER_FILE_ABS with REPO_ROOT.
# Computing these from `$REPO_ROOT/.claude/settings.json` BEFORE arg
# parsing would freeze SETTINGS_JSON at the script's own location and
# silently ignore `--repo=` (the test-check_worktree_admin_files.sh
# pattern uses the same order, but that script has no per-repo
# defaults to recompute).
if [ -z "$SETTINGS_JSON" ]; then
  SETTINGS_JSON="$REPO_ROOT/.claude/settings.json"
fi
if [ -z "$TESTS_DIR" ]; then
  TESTS_DIR="$REPO_ROOT/backend/tests"
fi
MARKER_FILE_ABS="$REPO_ROOT/$MARKER_FILE_REL"

if [ ! -d "$REPO_ROOT" ]; then
  echo "ERROR: repo root does not exist: $REPO_ROOT" >&2
  exit 2
fi

if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository: $REPO_ROOT" >&2
  exit 2
fi

if [ ! -f "$SETTINGS_JSON" ]; then
  # No tracked settings.json means no hook contract to enforce. Treat as
  # clean so the gate is harmless when the team runs in a minimal setup.
  echo "OK: no $SETTINGS_JSON found — no PreToolUse contract to enforce"
  exit 0
fi

# `PreToolUse` may be absent, the empty list, or a non-empty list of hook
# entries. We use Python (not jq) because this repo does not assume jq
# is installed — CLAUDE.md's "Gotchas" section explicitly notes the
# "no `.env` file needed" stance and tooling in CLAUDE.md sticks to
# Python or git. Read the JSON via the same Python interpreter that
# builds the venv backend tooling uses; fall back to a Python 3 on PATH
# if the shared venv is unreachable (e.g. a CI runner without the
# host's venv symlink).
# `command -v` returns 0 with the resolved path on stdout when the name
# is on PATH; `[ -x "$PY_BIN" ]` on a plain name (no slashes) returns
# false even when the name IS on PATH, so the two-step check is the
# minimal pattern that works in both interactive shells (where `python3`
# may be a wrapper function) and CI runners (where it is a plain PATH
# entry).
PY_BIN="${PY_BIN:-python3}"
if command -v "$PY_BIN" >/dev/null 2>&1; then
  PY_BIN="$(command -v "$PY_BIN")"
elif [ -x "/usr/bin/python3" ]; then
  PY_BIN="/usr/bin/python3"
else
  echo "ERROR: python3 not found on PATH and /usr/bin/python3 missing" >&2
  exit 2
fi

# Returns 0 (success) and prints "EMPTY" or "POPULATED" on stdout when
# the script's existence checks pass. The body is a single Python
# invocation — no shell quoting of JSON, no risk of misinterpreting
# bracket characters as globs.
PRETOOLUSE_STATE=$("$PY_BIN" - "$SETTINGS_JSON" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError) as exc:
    print(f"ERROR: failed to parse {path}: {exc}", file=sys.stderr)
    sys.exit(2)

hooks = data.get("hooks") or {}
pretooluse = hooks.get("PreToolUse")
if not isinstance(pretooluse, list) or len(pretooluse) == 0:
    print("EMPTY")
else:
    print(f"POPULATED:{len(pretooluse)}")
PY
) || { echo "ERROR: pretooluse-state probe failed" >&2; exit 2; }

if [ "$PRETOOLUSE_STATE" = "EMPTY" ]; then
  printf 'OK: no PreToolUse hooks in %s — background-agent-test gate is a no-op\n' "$SETTINGS_JSON"
  exit 0
fi

# Non-empty PreToolUse list. Verify the contract: a marker file OR a
# test file. Evaluate both; either presence resolves the gate.
HAS_MARKER=0
if [ -e "$MARKER_FILE_ABS" ]; then
  HAS_MARKER=1
fi

HAS_TEST_FILE=0
if [ -d "$TESTS_DIR" ]; then
  # Case-insensitive substring match on both `pretooluse` and
  # `background` in the basename. The pattern is deliberately permissive
  # within that constraint — `test_pretooluse_bg_agent.py`,
  # `test_pretooluse_x_background_agent_fire.py`, etc. all match.
  # `find -iname` is the form that survives a non-POSIX `find`; the
  # bash `[[ ... =~ ]]` form is equally portable but harder to read.
  MATCH=$(find "$TESTS_DIR" -maxdepth 1 -type f -name '*.py' \
    -printf '%f\n' 2>/dev/null \
    | awk 'tolower($0) ~ /pretooluse/ && tolower($0) ~ /background/ { print; found = 1; exit } END { exit !found }') \
    || true
  if [ -n "$MATCH" ]; then
    HAS_TEST_FILE=1
  fi
fi

if [ "$HAS_MARKER" -eq 1 ] || [ "$HAS_TEST_FILE" -eq 1 ]; then
  SOURCES=""
  if [ "$HAS_MARKER" -eq 1 ]; then
    SOURCES="$SOURCES marker=$MARKER_FILE_REL"
  fi
  if [ "$HAS_TEST_FILE" -eq 1 ]; then
    SOURCES="$SOURCES test_match=$MATCH"
  fi
  printf 'OK: PreToolUse hook(s) present and background-agent-test contract met (%s)\n' "$SOURCES"
  exit 0
fi

echo "WARNING: $PRETOOLUSE_STATE PreToolUse hook(s) in $SETTINGS_JSON, but no background-agent-test detected." >&2
printf '  Neither %s nor a test file matching both `pretooluse` and `background` (case-insensitive) was found under %s.\n' \
  "$MARKER_FILE_REL" "$TESTS_DIR" >&2
cat >&2 <<'EOF'

Claude Code 2.1.222 (changelog 2026-08-04) fixed a permission bypass in
which PreToolUse auto-allow hooks silently dropped restrictions on
background-agent invocations (subagent, summary, compaction, rename).
A hook that "works" in foreground tests can still leak on the
background route — the exact failure class kanban card
513e37a1a86e41db8b6af8423292f6b6 documents (a write lands on the wrong
checkout without an error).

Fix: write a test that explicitly fires the hook from a background-agent
invocation. Then either:
  - touch .claude/hooks/pretooluse-bg-agent-test-pass  (a marker file
    the test author drops alongside the test), or
  - name the test file `test_pretooluse_*_background*.py` (case-
    insensitive substring match on both tokens).

Either signal satisfies the gate. See docs/cockpit/00-orientation.md
§"Hook contracts — testen op de achtergrond-route" for the full
rationale.
EOF

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
exit 0
