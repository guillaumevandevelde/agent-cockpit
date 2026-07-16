#!/usr/bin/env bash
#
# list-orphan-bridge-sessions.sh — report Agent Bridge tmux sessions that
# Cockpit spawned but that no kanban card currently claims.
#
# Both existing cleanup paths are card-scoped: session_cleanup.py fires on a
# card's Done/Impediment transition, and the dispatch reaper (reap_stale_claims)
# only reaps `agent:` claims it finds by iterating `cards`. A session spawned
# outside dispatch (the agent-bridge "New Session" dialog, a manual smoke test)
# never has a card, so neither path — nor SessionRegistry.get_stuck_sessions,
# which only recognises the `<project>/.claude/worktrees/<name>` cwd shape from
# a dispatch spawn — ever sees it. It keeps running, invisible, eating RAM.
# See docs/cockpit/spawn-test-bridge-sessions-analyse.md (bevinding 6).
#
# Detection, entirely from tmux + the kanban DB (no backend process needed):
#   1. "Cockpit-spawned"  — every call to spawn_session() (dispatch AND the
#      manual New Session dialog) sets COCKPIT_RUNTIME via `tmux new-session -e`
#      (see backend/app/services/agentic_cli/provider_env.py:build_spawn_env).
#      `tmux show-environment -t <session> COCKPIT_RUNTIME` is therefore a
#      reliable, backend-restart-proof marker — tmux's own env table is the
#      source of truth, not an in-memory dict.
#   2. "Claimed"          — reuses kanban_active_worktrees.py (the same query
#      worktree-gc.sh trusts to protect a live worktree): any card with
#      `claimed_by LIKE 'agent:%'` outside Done/Impediment. Session name ==
#      worktree name for every dispatch-transport session.
#   3. "Old enough"       — `#{session_created}` (tmux's own timestamp, not a
#      dict) must be at least ORPHAN_GRACE_S ago, so a session between spawn
#      and its card-claim being committed is never flagged.
#
# Orphan = Cockpit-spawned AND alive AND NOT claimed AND older than the grace
# window. This is DELIBERATELY REPORT-ONLY: there is no --apply / kill mode.
# A manually-spawned debug session is indistinguishable from a leaked test
# session by this script — killing on sight would repeat the worktree-gc
# postmortem ("actieve claim weggekilld") one layer down. A human decides;
# this script only makes the decision visible (see docs/cockpit/agent-bridge.md).
#
# Usage:
#   scripts/list-orphan-bridge-sessions.sh
#   scripts/list-orphan-bridge-sessions.sh -h|--help
#
# Environment:
#   KANBAN_DB       Path to kanban.db (default: ~/.claude-registry/kanban.db)
#   ORPHAN_GRACE_S  Minimum session age in seconds before flagging (default: 120)
#
set -uo pipefail

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '/^!/d'
      exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_DB="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"
ORPHAN_GRACE_S="${ORPHAN_GRACE_S:-120}"

# 1. Live tmux sessions with their creation epoch. Absent tmux / no server
# running are both "zero live sessions" — not an error, nothing to flag.
sessions=()
if command -v tmux >/dev/null 2>&1; then
  while IFS=' ' read -r name created; do
    [ -n "$name" ] || continue
    sessions+=("$name $created")
  done < <(tmux list-sessions -F "#{session_name} #{session_created}" 2>/dev/null || true)
fi

# 2. Claimed session names (agent: claim outside Done/Impediment).
declare -A CLAIMED=()
if [ -r "$KANBAN_DB" ]; then
  while IFS=$'\t' read -r wt_name _branch; do
    [ -n "$wt_name" ] || continue
    CLAIMED["$wt_name"]=1
  done < <(python3 "$SCRIPT_DIR/kanban_active_worktrees.py" --db "$KANBAN_DB" 2>/dev/null || true)
fi

now="$(date +%s)"
flagged=0

for entry in "${sessions[@]:-}"; do
  [ -n "$entry" ] || continue
  name="${entry%% *}"
  created="${entry#* }"

  # Not Cockpit-spawned at all — out of scope, never our concern.
  tmux show-environment -t "$name" COCKPIT_RUNTIME >/dev/null 2>&1 || continue

  # A live kanban claim owns this session.
  [ -n "${CLAIMED[$name]:-}" ] && continue

  age=$(( now - created ))
  if [ "$age" -lt "$ORPHAN_GRACE_S" ]; then
    continue
  fi

  printf 'WOULD-FLAG %-30s  age=%ds  no active kanban claim\n' "$name" "$age"
  flagged=$((flagged + 1))
done

echo
if [ "$flagged" -gt 0 ]; then
  echo "list-orphan-bridge-sessions: $flagged orphan bridge session(s) found — report-only, nothing killed."
else
  echo "list-orphan-bridge-sessions: no orphan bridge sessions found."
fi
exit 0
