#!/usr/bin/env bash
#
# check-worktree-admin-files.sh — flag git's own per-worktree admin files
# when they are TRACKED in the repository.
#
# Rationale (kanban card 7dd8a3dd…). Ten files whose names are exactly git's
# per-worktree admin filenames — AUTO_MERGE, HEAD, MERGE_HEAD, MERGE_MODE,
# MERGE_MSG, ORIG_HEAD, commondir, gitdir, index, index.lock — got committed
# to the repo root and broke *every* subsequent ship through the documented
# direct-mode recipe. Two independent failure modes, both silent-looking:
#
#   1. `git worktree add` into a path under `.git/worktrees/` checked the
#      tracked files out ON TOP of git's live admin files for that worktree,
#      producing `fatal: .../index: index file smaller than expected`. The
#      conflict carve-out then read an EMPTY conflict set, failed its
#      equality check, and fell through to report_impediment with
#      `conflicted: ` (blank) — a diagnosis that points nowhere.
#   2. A tracked `HEAD` made `HEAD` ambiguous repo-wide:
#      `git diff --quiet HEAD` exits 128 with `fatal: ambiguous argument
#      'HEAD': both revision and filename`. The ship pre-flight uses exactly
#      that command under `if ! ...`, so a 128 reads as "dirty tree" and
#      EVERY session aborted with a bogus `uncommitted changes` error before
#      even reaching the merge.
#
# How they got in: the recipe placed its throwaway merge worktree *inside*
# `.git/worktrees/ship-merge-$$` (deliberately, to dodge /tmp reaping — card
# 01aa1ef5…), and the conflict carve-out ran `git -C "$WT" add -A`. That
# stages relative to the worktree root, which for that path IS git's admin
# directory. One ship through the conflict branch committed all ten.
#
# Both root causes are fixed in the recipe itself (worktree moved out of
# `.git/`, `add -A` replaced by an explicit path list, pre-flight
# disambiguated with `--`), and `.gitignore` carries root-anchored entries.
# This gate is the belt-and-braces third layer: `.gitignore` does not stop
# `git add -f`, and a future recipe edit could reintroduce a blind `add`.
# Because the blast radius is "no card can ship at all", a cheap mechanical
# check is worth having.
#
# TRACKED-only, deliberately. An UNTRACKED file named `index` or `HEAD` in the
# repo root is harmless — it is ignored by `.gitignore`, it never lands on
# master, and it cannot be checked out over a fresh worktree's admin files.
# Flagging those would false-positive on any session that happens to run a
# tool writing such a name into the tree. `git ls-files` (the index) is the
# exact predicate for "will this reach master".
#
# Root-anchored, deliberately. `frontend/src/index.css`, `docs/index.md`, a
# Python package's `index.py` — all legitimate and all irrelevant: git only
# reads these names at the ROOT of a worktree. A repo-wide name match would
# be noise. The check pins each name to the top level.
#
# Usage:
#   scripts/check-worktree-admin-files.sh [--strict] [--repo=PATH] [--help]
#
# Env:
#   REPO_ROOT   repo root to check (default: parent of SCRIPT_DIR)
#
# Exit codes:
#   0  clean OR (advisory mode and >=1 hit)
#   1  --strict and >=1 hit
#   2  usage error / repo missing / not a git repo

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"
STRICT=0

# The exact set of filenames git writes into a per-worktree admin directory.
# Sourced from the ten files found tracked on master by card 7dd8a3dd…, which
# is the full content of a `.git/worktrees/<name>/` dir captured mid-merge.
ADMIN_FILES=(
  AUTO_MERGE
  HEAD
  MERGE_HEAD
  MERGE_MODE
  MERGE_MSG
  ORIG_HEAD
  commondir
  gitdir
  index
  index.lock
)

usage() {
  sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --repo=*) REPO_ROOT="${arg#--repo=}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "usage error: unknown argument '$arg'" >&2; usage >&2; exit 2 ;;
  esac
done

if [ ! -d "$REPO_ROOT" ]; then
  echo "ERROR: repo root does not exist: $REPO_ROOT" >&2
  exit 2
fi

if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository: $REPO_ROOT" >&2
  exit 2
fi

# `git ls-files -- <name>` with no leading slash and no wildcard matches the
# path relative to the repo root only, which is exactly the anchoring we want.
# Passing all ten in one call keeps this to a single git invocation.
HITS="$(git -C "$REPO_ROOT" ls-files -- "${ADMIN_FILES[@]}" 2>/dev/null | LC_ALL=C sort -u)"

if [ -z "$HITS" ]; then
  echo "OK: no git per-worktree admin files are tracked in the repo root"
  exit 0
fi

COUNT="$(printf '%s\n' "$HITS" | grep -c .)"

echo "WARNING: $COUNT git per-worktree admin file(s) tracked in the repo root:" >&2
printf '%s\n' "$HITS" | sed 's/^/  - /' >&2
cat >&2 <<'EOF'

These are git's own files, not project files. While tracked, they break the
direct-mode ship recipe for EVERY card:
  - `git worktree add` into `.git/worktrees/<name>` checks them out over git's
    live admin files -> "fatal: index file smaller than expected"
  - a tracked `HEAD` makes `git diff --quiet HEAD` exit 128
    ("ambiguous argument 'HEAD': both revision and filename"), so the ship
    pre-flight reports a bogus "uncommitted changes" abort

Fix: `git rm -f <files>` (note: plain `rm` is deny-listed in this repo), then
verify `.gitignore` still carries the root-anchored entries (/HEAD, /index, ...).
See kanban card 7dd8a3dd… for the full incident.
EOF

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
exit 0
