"""Render the CC hook command that POSTs session events to the backend.

Install by adding entries to ~/.claude/settings.json under "hooks" for the
UserPromptSubmit, Stop, Notification, and SessionStart events. The hook reads
the JSON CC passes on stdin (contains session_id + cwd) and forwards it.
Requires `jq` and `curl` in the session environment (WSL Ubuntu has both).
"""
import json
import logging

logger = logging.getLogger(__name__)

def render_hook_command(event: str, port: int = 8000) -> str:
    url = f"http://localhost:{port}/api/v1/scheduled-messages/hook-event"
    if event == "Notification":
        # Claude Code 2.1.198+ adds `notification_type` to Notification events
        # (e.g. agent_needs_input, agent_completed). Forward it so the router
        # can branch on the structured field rather than substring-matching
        # `message`; older CC payloads simply have `null` for that key, which
        # is fine — `auto_resume.classify_notification` falls back to
        # substring matching when notification_type is absent.
        return (
            f"jq -c --arg ev {json.dumps(event)} '{{event:$ev, session_id:.session_id, cwd:.cwd, "
            "tmux_pane:env.TMUX_PANE, message:.message, notification_type:.notification_type}' "
            f"| curl -s -X POST -H 'Content-Type: application/json' -d @- {url} >/dev/null 2>&1 || true"
        )
    return (
        f"jq -c --arg ev {json.dumps(event)} '{{event:$ev, session_id:.session_id, cwd:.cwd, tmux_pane:env.TMUX_PANE}}' "
        f"| curl -s -X POST -H 'Content-Type: application/json' -d @- {url} >/dev/null 2>&1 || true"
    )


def settings_hooks_block(port: int = 8000) -> dict:
    """Return a dict to merge into ~/.claude/settings.json 'hooks'."""
    def entry(ev: str):
        return [{"hooks": [{"type": "command", "command": render_hook_command(ev, port)}]}]
    return {
        "UserPromptSubmit": entry("UserPromptSubmit"),
        "Stop": entry("Stop"),
        "Notification": entry("Notification"),
        "SessionStart": entry("SessionStart"),
    }
