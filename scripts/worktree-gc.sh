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
# A worktree is REMOVED only when BOTH are true:
#   1. its working tree is CLEAN (no uncommitted / untracked changes), and
#   2. its branch is fully merged into master (every commit's patch is already
#      in master — detected with `git cherry`, so squash-merges count as merged).
#
# Anything dirty or carrying unmerged commits is KEPT and reported. The bare
# top-level checkout and the `master` branch are never touched.
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
# or from inside any linked worktree.
ROOT="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
ROOT="$(dirname "$ROOT")"
cd "$ROOT"

if ! git rev-parse --verify --quiet "$BASE_BRANCH" >/dev/null; then
  echo "error: base branch '$BASE_BRANCH' not found" >&2
  exit 1
fi

echo "worktree-gc: base=$BASE_BRANCH  mode=$([ "$APPLY" = 1 ] && echo APPLY || echo dry-run)"
echo

git worktree prune

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
