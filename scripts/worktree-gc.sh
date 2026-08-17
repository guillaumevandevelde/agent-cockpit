#!/usr/bin/env bash
#
# worktree-gc.sh — garbage-collect stale git worktrees and branches
#
# Safety net for the "mess of leftover worktrees" problem: over time, dispatched
# sessions (kanban / engineer agents) and manual worktrees pile up. The kanban
# backend auto-removes a worktree when its card reaches Done, but that only fires
# for cards that actually reach Done — merged-but-not-Done or manually created
# worktrees leak. This script reclaims the leaked ones, safely.
#
# It runs in two passes:
#   Pass 1 — worktrees under .claude/worktrees/ (plus their branch).
#   Pass 2 — orphan branches: local branches with no worktree left. The Done-move
#            teardown removes the worktree but keeps the branch on purpose (the
#            ship-recipe wants it for redispatch/resume), which put every shipped
#            card's branch permanently out of pass 1's reach. Pass 2 is the only
#            thing that reclaims those.
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
# touched. Pass 2 applies the same guards except "clean", which is meaningless
# without a checkout.
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
  #    `grep -c` rather than `grep -q` — see the note in pass 2: -q exits early,
  #    SIGPIPEs `git cherry`, and `set -o pipefail` then turns a successful
  #    match into a failed pipeline, reading an unmerged branch as merged.
  if [ "$is_base" = 0 ]; then
    if [ "$(git cherry "$BASE_BRANCH" "$branch" 2>/dev/null | grep -c '^+' || true)" -gt 0 ]; then
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

# ---------------------------------------------------------------------------
# Pass 2 — orphan branches: local branches with no worktree at all.
#
# The loop above is worktree-driven, so it can only ever see a branch that
# still has a checkout. But the backend's Done-move teardown
# (`session_cleanup._remove_worktree_at`) removes the worktree and
# deliberately keeps the branch — the ship-recipe wants it alive for
# redispatch/resume. From that moment the branch is invisible to pass 1 and
# nothing else ever reclaims it, so every shipped card left one behind: 82
# dead local branches had piled up on the live repo when this pass was added.
#
# Same four guards as pass 1 (active claim, live lease, merged) minus the
# clean-worktree check, which has no meaning without a checkout.
# ---------------------------------------------------------------------------
# Two ways a live worktree lays claim to a branch name, and pass 2 must honour
# both: the branch it has checked out, and — for a detached checkout, which
# prints `detached` instead of a `branch` line — the directory name the
# transport derived from the branch. Missing the second one made pass 2 report
# a still-occupied branch as an orphan.
declare -A CHECKED_OUT=()
while IFS= read -r porcelain_line; do
  case "$porcelain_line" in
    "branch refs/heads/"*) CHECKED_OUT["${porcelain_line#branch refs/heads/}"]=1 ;;
    "worktree "*)          CHECKED_OUT["$(basename "${porcelain_line#worktree }")"]=1 ;;
  esac
done < <(git worktree list --porcelain)

while IFS= read -r branch; do
  # Never the base branch, and never anything pass 1 already owns.
  [ "$branch" = "$BASE_BRANCH" ] && continue
  [ -n "${CHECKED_OUT[$branch]:-}" ] && continue

  # Leases and claims are keyed by session name, which is exactly the branch
  # name the worktree transport creates (`git worktree add -b "$session_name"`).
  if [ -n "${ACTIVE_WTS[$branch]:-}" ]; then
    printf 'KEEP   %-40s  active claim (kanban card agent:%s still alive)\n' \
      "$branch" "$branch"
    kept=$((kept+1)); continue
  fi

  if [ -n "${LEASED_WTS[$branch]:-}" ] && is_lease_live "${LEASE_EXPIRIES[$branch]}"; then
    printf 'KEEP   %-40s  live lease (owner=%s, expires %s)\n' \
      "$branch" "${LEASE_OWNERS[$branch]}" "${LEASE_EXPIRIES[$branch]}"
    kept=$((kept+1)); continue
  fi

  # `grep -c`, not `grep -q`: -q exits on the first match, which SIGPIPEs
  # `git cherry`, and under `set -o pipefail` the pipeline then reports
  # failure even though the match succeeded — i.e. an unmerged branch would
  # read as merged and get deleted. -c drains the input, so the status is
  # honest. (Only reachable for outputs large enough to fill the pipe buffer,
  # which is precisely the many-unmerged-commits branch we must not lose.)
  if [ "$(git cherry "$BASE_BRANCH" "$branch" 2>/dev/null | grep -c '^+' || true)" -gt 0 ]; then
    printf 'KEEP   %-40s  unmerged commits on %s (no worktree)\n' "$branch" "$branch"
    kept=$((kept+1)); continue
  fi

  if [ "$APPLY" = 1 ]; then
    git branch -D "$branch" >/dev/null
    printf 'REMOVED-BRANCH %-31s  merged, no worktree\n' "$branch"
    if [ -n "${LEASED_WTS[$branch]:-}" ]; then
      python3 "$(dirname "$0")/kanban_worktree_leases.py" --db "$KANBAN_DB" --clear "$branch" 2>/dev/null || true
    fi
  else
    printf 'WOULD-REMOVE-BRANCH %-26s  merged, no worktree\n' "$branch"
  fi
  removed=$((removed+1))
done < <(git for-each-ref --format='%(refname:short)' refs/heads/)

echo
echo "worktree-gc: $removed to-remove, $kept kept."
if [ "$APPLY" = 0 ] && [ "$removed" -gt 0 ]; then
  echo "Re-run with --apply to remove them."
fi
