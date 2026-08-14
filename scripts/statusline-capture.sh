#!/usr/bin/env bash
#
# Capture Claude Code's official rate limits, then render the real statusline.
#
# Claude Code passes a JSON blob on stdin to whatever `statusLine.command`
# is configured. Since CC 2.1.x that blob carries `rate_limits` — the only
# place Anthropic exposes subscription quota for Pro/Max. It is not on disk
# anywhere, not in hook payloads, and there is no API to poll (verified
# 2026-08-14; see docs/cockpit/subscription-usage-decision.md). So the
# statusline is the one channel, and this wrapper taps it in passing.
#
# Usage — wrap your existing statusline command by putting it after this
# script's own path in ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "bash /path/to/scripts/statusline-capture.sh npx -y @owloops/claude-powerline@latest --theme=dark --style=powerline"
#   }
#
# DESIGN RULE: this script must never be able to break the prompt. It runs
# on every statusline render, which is constant during a session, and a
# broken statusline is visible in every terminal at once. So:
#
#   - no `set -e`: a failed capture must not abort the render
#   - the whole capture block is wrapped and silenced; its exit status is
#     discarded
#   - the downstream command runs unconditionally afterwards, with the
#     original stdin replayed byte-for-byte
#   - the state write is atomic (tmp + mv), so a reader never sees a
#     half-written file, and a crash mid-write leaves the previous
#     snapshot intact
#
# If jq is missing the capture silently does nothing and the prompt still
# renders — degrading to "no signal" is the designed failure mode.

set -uo pipefail

STATE_DIR="${COCKPIT_REGISTRY_DIR:-$HOME/.claude-registry}"
STATE_FILE="$STATE_DIR/rate-limits.json"

# stdin is readable exactly once — buffer it before anyone consumes it.
input=$(cat)

{
  if [ -n "$input" ] && command -v jq >/dev/null 2>&1; then
    mkdir -p "$STATE_DIR"
    tmp="$STATE_FILE.$$.tmp"
    # `captured_at` is ours, not Anthropic's: the reader needs to know how
    # old this reading is, and the payload carries no timestamp of its own.
    if printf '%s' "$input" | jq -c '{
          captured_at: (now | todate),
          subscription_type: .subscription_type,
          rate_limits_available: .rate_limits_available,
          rate_limits: .rate_limits
        }' >"$tmp" 2>/dev/null; then
      mv -f "$tmp" "$STATE_FILE"
    else
      rm -f "$tmp"
    fi
  fi
} >/dev/null 2>&1 || true

# Render the real statusline. No arguments means capture-only, which is
# what the offline test harness uses.
if [ "$#" -gt 0 ]; then
  printf '%s' "$input" | "$@"
fi
