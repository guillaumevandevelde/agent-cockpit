#!/usr/bin/env bash
#
# cockpit-doctor.sh — read-only health check for the dangerous repo states that
# once cascaded into a wiped master. Prints PASS/WARN/FAIL per check; exits
# non-zero only when a FAIL (something actively broken) is found. Never writes,
# never fetches, never touches the remote.
#
# Checks:
#   1. bare-sanity     — core.bare=true while a working copy exists on disk
#   2. local tree size — HEAD/master collapsed to a tiny tree (the a.txt wipe)
#   3. remote tree size— origin/master collapsed to a tiny tree
#   4. stale checkout  — working copy is missing many files origin/master has
#   5. worktree leaks  — merged+clean worktrees left lying around
#   6. test-project rows — leftover "mcp-test-*" rows in claude_registry.db
#   7. orphan bridge sessions — Cockpit-spawned tmux sessions with no live kanban claim
#   8. litellm sidecar — opt-in: `PASS` wanneer geen sidecar, anders `WARN` bij
#                         hardening-FAILs uit scripts/check-litellm-hardening.sh
#
# Usage: scripts/cockpit-doctor.sh
set -uo pipefail

FLOOR=50          # a healthy tree has ~700 files; below this = almost certainly a wipe
STALE_MISSING=10  # this many origin/master files absent on disk = stale/wrong checkout

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$ROOT" ] && ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
cd "$ROOT" || { echo "cockpit-doctor: not in a git repo"; exit 2; }

bold=$'\033[1m'; red=$'\033[31m'; grn=$'\033[32m'; ylw=$'\033[33m'; rst=$'\033[0m'
worst=0   # 0=ok, 1=warn, 2=fail
pass() { printf '  %sPASS%s %s\n' "$grn" "$rst" "$1"; }
warn() { printf '  %sWARN%s %s\n' "$ylw" "$rst" "$1"; [ "$worst" -lt 1 ] && worst=1; }
crit() { printf '  %sFAIL%s %s\n' "$red" "$rst" "$1"; worst=2; }

tree_count() { git ls-tree -r --name-only "$1" 2>/dev/null | wc -l | tr -d ' '; }

printf '%scockpit-doctor%s  (%s)\n' "$bold" "$rst" "$ROOT"

# 1. bare-sanity
if [ "$(git config core.bare)" = "true" ] && [ -f "$ROOT/CLAUDE.md" ]; then
    warn "core.bare=true but a working copy exists on disk — repo should be a normal checkout (git config core.bare false)."
else
    pass "repo mode sane (core.bare=$(git config core.bare))."
fi

# 2. local master/HEAD tree size
ref=master; git rev-parse --verify -q master >/dev/null || ref=HEAD
n=$(tree_count "$ref")
if [ "${n:-0}" -lt "$FLOOR" ]; then
    crit "$ref tree has only ${n:-0} files (< $FLOOR) — looks CLOBBERED. Recover: git update-ref refs/heads/master <last-good>."
else
    pass "$ref tree healthy ($n files)."
fi

# 3. origin/master tree size (remote-tracking ref; no fetch)
if git rev-parse --verify -q origin/master >/dev/null; then
    rn=$(tree_count origin/master)
    if [ "${rn:-0}" -lt "$FLOOR" ]; then
        crit "origin/master tree has only ${rn:-0} files (< $FLOOR) — REMOTE clobbered. Fix, then: git push --force-with-lease origin master."
    else
        pass "origin/master tree healthy ($rn files)."
    fi
else
    pass "no origin/master tracking ref (skipped)."
fi

# 4. stale checkout — files origin/master has that are missing on disk
if git rev-parse --verify -q origin/master >/dev/null; then
    missing=$(git diff --diff-filter=D --name-only origin/master -- 2>/dev/null | wc -l | tr -d ' ')
    if [ "${missing:-0}" -ge "$STALE_MISSING" ]; then
        warn "working copy is missing $missing files that origin/master has — stale/wrong checkout. Align: git reset --hard origin/master (back up first)."
    else
        pass "working copy in step with origin/master ($missing files missing)."
    fi
fi

# 5. worktree leaks (reuse the gc dry-run)
if [ -x "$ROOT/scripts/worktree-gc.sh" ]; then
    leaks=$("$ROOT/scripts/worktree-gc.sh" 2>/dev/null | grep -c '^WOULD-REMOVE')
    if [ "${leaks:-0}" -gt 0 ]; then
        warn "$leaks merged+clean worktree(s) left over — run scripts/worktree-gc.sh --apply."
    else
        pass "no leftover worktrees."
    fi
fi

# 6. leftover test-project rows (reuse the cleanup script's dry-run)
if [ -x "$ROOT/scripts/cleanup-test-projects.sh" ]; then
    stale=$("$ROOT/scripts/cleanup-test-projects.sh" 2>/dev/null | grep -c '^WOULD-REMOVE')
    if [ "${stale:-0}" -gt 0 ]; then
        warn "$stale leftover test-project row(s) in claude_registry.db — run scripts/cleanup-test-projects.sh --apply."
    else
        pass "no leftover test-project rows."
    fi
fi

# 7. orphan bridge sessions — Cockpit-spawned tmux sessions no card claims
# (docs/cockpit/spawn-test-bridge-sessions-analyse.md bevinding 6). Report-only:
# the script never kills anything, so this check can never surprise a live
# manual debug session — see docs/cockpit/agent-bridge.md.
if [ -x "$ROOT/scripts/list-orphan-bridge-sessions.sh" ]; then
    orphans=$("$ROOT/scripts/list-orphan-bridge-sessions.sh" 2>/dev/null | grep -c '^WOULD-FLAG')
    if [ "${orphans:-0}" -gt 0 ]; then
        warn "$orphans orphan agent-bridge session(s) with no active kanban claim — run scripts/list-orphan-bridge-sessions.sh for details."
    else
        pass "no orphan agent-bridge sessions."
    fi
fi

# 8. LiteLLM sidecar hardening (opt-in). Hergebruikt dezelfde check die de
# sidecar zelf verifieert (scripts/check-litellm-hardening.sh) — zelfde patroon
# als checks 5/6/7 die een ánder script in dry-run draaien en regels tellen.
# Doctor zou geen eigen `curl`-logica moeten schrijven: dat zet een tweede,
# zwakkere waarheid naast de 33-assert-check die al bestaat. Een dode sidecar
# krijgt WARN, niet FAIL — doctor reserveert exit-1 voor actief kapotte
# repo-staat (scripts/cockpit-doctor.sh:113-114 hieronder).
LITELLM_CONFIG_PATH="${LITELLM_CONFIG_PATH:-$ROOT/config/litellm/config.yaml}"
if [ ! -f "$LITELLM_CONFIG_PATH" ]; then
    # Geen sidecar geconfigureerd: sla over (geen permanente WARN voor boxen
    # die de sidecar niet gebruiken — zou mensen alleen maar trainen om
    # doctor-output weg te kijken).
    pass "no litellm sidecar configured (check skipped — opt-in)."
elif [ ! -x "$ROOT/scripts/check-litellm-hardening.sh" ]; then
    warn "litellm sidecar configured but scripts/check-litellm-hardening.sh missing — kan niet verifiëren."
else
    # Draai de hardening-check in advisory modus. We geven het config-pad mee
    # zodat properties 3-5 (no-prompt-mutation, no-telemetry, credentials) ook
    # gecontroleerd worden; anders zou die drie overslaan (zie de check zelf:
    # "config-checks: no --config-yaml given — checks 3 ... are SKIPPED").
    HARDENING_OUT=$("$ROOT/scripts/check-litellm-hardening.sh" \
        --config-yaml "$LITELLM_CONFIG_PATH" 2>/dev/null) || true
    # Strip ANSI kleurcodes rond `FAIL`/`WARN`/`PASS`-markers — anders matcht
    # `^[[:space:]]+FAIL ` niet: de echte regel is "  \x1b[31mFAIL\x1b[0m ...".
    HARDENING_FAILS=$(printf '%s\n' "$HARDENING_OUT" \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | grep -cE '^[[:space:]]+FAIL ' || true)
    if [ "${HARDENING_FAILS:-0}" -gt 0 ]; then
        warn "litellm sidecar hardening: $HARDENING_FAILS FAIL(s) — run scripts/check-litellm-hardening.sh --strict (with --config-yaml $LITELLM_CONFIG_PATH) for details."
    else
        pass "litellm sidecar hardening clean (no FAILs reported; reachability included)."
    fi
fi

case "$worst" in
    0) printf '%sall checks passed.%s\n' "$grn" "$rst" ;;
    1) printf '%sfinished with warnings.%s\n' "$ylw" "$rst" ;;
    2) printf '%sFAILURES found — see above.%s\n' "$red" "$rst" ;;
esac
# Only a FAIL (actively broken state) is non-zero; WARNs are advisory.
[ "$worst" -ge 2 ] && exit 1
exit 0
