#!/usr/bin/env bash
#
# worktree-gc.sh — garbage-collect stale git worktrees under .claude/worktrees/
#
# Safety net for the "mess of leftover worktrees" problem: over time, dispatched
# sessions (kanban / engineer agents) and manual worktrees pile up. The kanban
# backend auto-removes a worktree when its card reaches Done, but that only fires
# for cards that actually reach Done — merged-but-not-Done or manually created
# worktrees leak. This script reclaims the leaked ones, safely.
#
# A worktree is REMOVED only when ALL of the following are true:
#   1. no kanban card currently holds an active agent claim on it. A kanban
#      card whose `claimed_by` is `agent:<worktree_name>` and whose column is
#      NOT Done/Impediment is a live session that would be killed by removal —
#      exactly the failure this check guards against (see the
#      "worktree-gc verwijdert branch/worktree van actieve analyst-sessie"
#      postmortem). An Analyst-only session never commits, so its branch is
#      trivially "merged+clean" from the moment it's created — without this
#      guard, the next gc run kills it.
#   2. no live worktree lease exists. Each spawn writes
#      `worktree_lease:<name>` + `worktree_owner:<name>` rows in KanbanMeta
#      with a TTL (default 24h). The lease is the hard signal that
#      distinguishes "kill -9 orphan" from "currently in use" — without it,
#      a fresh dispatch that re-uses a worktree name from a previous
#      orphaned session would be silently clobbered. An expired lease is
#      treated as no lease and is cleared on successful removal.
#   3. its working tree is CLEAN (no uncommitted / untracked changes),
#   4. its branch is fully merged into master (every commit's patch is already
#      in master — detected with `git cherry`, so squash-merges count as merged).
#
# Anything dirty, unmerged, actively claimed, or under a live lease is KEPT
# and reported. The bare top-level checkout and the `master` branch are never
# touched.
#
# Usage:
#   scripts/worktree-gc.sh            # dry-run: show what WOULD be removed (default)
#   scripts/worktree-gc.sh --apply    # actually remove merged+clean worktrees
#   scripts/worktree-gc.sh -h|--help
#
set -euo pipefail

BASE_BRANCH="master"
WORKTREE_DIR=".claude/worktrees"
APPLY=0

for arg in "$@"; do
  case "$arg" in
    --apply|--yes|-y) APPLY=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '/^!/d'
      exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Resolve the repository (common) root as an ABSOLUTE path so it matches the
# absolute paths that `git worktree list` prints. Works from the bare top-level
# or from inside any linked worktree. WORKTREE_GC_ROOT (env) overrides the
# discovery — used by the bash test harness so each test can run against an
# isolated temp git repo instead of touching the live checkout.
if [ -n "${WORKTREE_GC_ROOT:-}" ]; then
  ROOT="$(cd "$WORKTREE_GC_ROOT" && pwd -P)"
else
  ROOT="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
  ROOT="$(dirname "$ROOT")"
fi
cd "$ROOT"

if ! git rev-parse --verify --quiet "$BASE_BRANCH" >/dev/null; then
  echo "error: base branch '$BASE_BRANCH' not found" >&2
  exit 1
fi

echo "worktree-gc: base=$BASE_BRANCH  mode=$([ "$APPLY" = 1 ] && echo APPLY || echo dry-run)"
echo

git worktree prune

# Kanban DB lives at ~/.claude-registry/kanban.db by default (see
# backend/app/config.py:_default_kanban_database_url). The helper script
# accepts an explicit path so tests can inject a temp DB; if it's missing
# or unreadable the helper prints nothing and we fall through to the
# merge+clean logic — never crash gc on a board-side outage.
KANBAN_DB="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"
declare -A ACTIVE_WTS=()
if [ -r "$KANBAN_DB" ]; then
  while IFS=$'\t' read -r wt_name wt_branch; do
    [ -n "$wt_name" ] || continue
    ACTIVE_WTS["$wt_name"]=1
  done < <(python3 "$(dirname "$0")/kanban_active_worktrees.py" --db "$KANBAN_DB" 2>/dev/null || true)
fi

# Live worktree leases (kanban-meta `worktree_lease:<name>` rows). Each value
# is `<owner>\t<iso_expiry>` so the gc script can decide live vs. expired
# without round-tripping into Python for every worktree. Keyed by worktree
# name; empty/absent = no lease (the pre-lease fallback applies).
declare -A LEASED_WTS=()
declare -A LEASE_OWNERS=()
declare -A LEASE_EXPIRIES=()
if [ -r "$KANBAN_DB" ]; then
  while IFS=$'\t' read -r wt_name owner expiry; do
    [ -n "$wt_name" ] || continue
    [ -n "$expiry" ] || continue
    LEASED_WTS["$wt_name"]=1
    LEASE_OWNERS["$wt_name"]="$owner"
    LEASE_EXPIRIES["$wt_name"]="$expiry"
  done < <(python3 "$(dirname "$0")/kanban_worktree_leases.py" --db "$KANBAN_DB" 2>/dev/null || true)
fi

is_lease_live() {
  # Decide whether an ISO-8601 expiry is still in the future. The strings
  # come from `datetime.now(timezone.utc).isoformat()` /
  # `datetime.fromisoformat(...)` so they parse with Python's stdlib. We
  # delegate to a small Python one-liner so the script stays portable across
  # GNU/BSD coreutils and macOS / WSL date flag differences.
  local expiry="$1"
  python3 - "$expiry" <<'PY'
import sys
from datetime import datetime, timezone
try:
    parsed = datetime.fromisoformat(sys.argv[1])
except (TypeError, ValueError):
    sys.exit(1)
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
sys.exit(0 if parsed > datetime.now(timezone.utc) else 1)
PY
}

removed=0 kept=0

# Iterate worktree paths (skip the bare main checkout, which has no branch).
while IFS= read -r line; do
  path="${line%% *}"
  case "$path" in
    "$ROOT") continue ;;                 # bare / main checkout
    "$ROOT/$WORKTREE_DIR"/*) : ;;        # only manage worktrees under .claude/worktrees
    *) continue ;;
  esac

  branch="$(git -C "$path" branch --show-current 2>/dev/null || true)"

  # Never delete the base branch; if a worktree is parked on master, we only
  # remove the redundant checkout, never the branch itself.
  is_base=0
  [ "$branch" = "$BASE_BRANCH" ] && is_base=1

  # 0. Active kanban claim? (analyst/engineer session still alive)
  wt_name="$(basename "$path")"
  if [ -n "${ACTIVE_WTS[$wt_name]:-}" ]; then
    printf 'KEEP   %-40s  active claim (kanban card agent:%s still alive)\n' \
      "$wt_name" "$wt_name"
    kept=$((kept+1)); continue
  fi

  # 0a. Live worktree lease? (kill -9 / host crash safety net)
  # The lease is keyed by worktree name; an expired lease is treated as
  # no lease and is dropped on successful removal (see the apply branch).
  if [ -n "${LEASED_WTS[$wt_name]:-}" ]; then
    expiry="${LEASE_EXPIRIES[$wt_name]}"
    if is_lease_live "$expiry"; then
      printf 'KEEP   %-40s  live lease (owner=%s, expires %s)\n' \
        "$wt_name" "${LEASE_OWNERS[$wt_name]}" "$expiry"
      kept=$((kept+1)); continue
    fi
    # expired lease — fall through and clean up; clear the row on success
  fi

  # 1. Clean?
  if [ -n "$(git -C "$path" status --porcelain)" ]; then
    printf 'KEEP   %-40s  dirty (uncommitted changes)\n' "$(basename "$path")"
    kept=$((kept+1)); continue
  fi

  # 2. Fully merged? (no '+' lines from git cherry == every commit is in base)
  if [ "$is_base" = 0 ]; then
    if git cherry "$BASE_BRANCH" "$branch" 2>/dev/null | grep -q '^+'; then
      printf 'KEEP   %-40s  unmerged commits on %s\n' "$(basename "$path")" "$branch"
      kept=$((kept+1)); continue
    fi
  fi

  # Eligible for removal.
  if [ "$APPLY" = 1 ]; then
    git worktree remove --force "$path"
    if [ "$is_base" = 0 ] && [ -n "$branch" ]; then
      git branch -D "$branch" >/dev/null
      printf 'REMOVED %-39s  worktree + branch %s\n' "$(basename "$path")" "$branch"
    else
      printf 'REMOVED %-39s  worktree (kept branch %s)\n' "$(basename "$path")" "$branch"
    fi
    # Drop any expired lease row so the next gc run sees no stale lease.
    if [ -n "${LEASED_WTS[$wt_name]:-}" ]; then
      python3 "$(dirname "$0")/kanban_worktree_leases.py" --db "$KANBAN_DB" --clear "$wt_name" 2>/dev/null || true
    fi
  else
    if [ "$is_base" = 0 ]; then
      printf 'WOULD-REMOVE %-34s  merged+clean (worktree + branch %s)\n' "$(basename "$path")" "$branch"
    else
      printf 'WOULD-REMOVE %-34s  redundant master checkout (worktree only)\n' "$(basename "$path")"
    fi
  fi
  removed=$((removed+1))
done < <(git worktree list | tail -n +2)

echo
echo "worktree-gc: $removed to-remove, $kept kept."
if [ "$APPLY" = 0 ] && [ "$removed" -gt 0 ]; then
  echo "Re-run with --apply to remove them."
fi
