"""Render the CC hook command that POSTs session events to the backend.

Install by adding entries to ~/.claude/settings.json under "hooks" for the
UserPromptSubmit, Stop, Notification, and SessionStart events. The hook reads
the JSON CC passes on stdin (contains session_id + cwd) and forwards it.
Requires `jq` and `curl` in the session environment (WSL Ubuntu has both).
"""
import json


def render_hook_command(event: str, port: int = 8000) -> str:
    url = f"http://localhost:{port}/api/v1/scheduled-messages/hook-event"
    return (
        "jq -c --arg ev %s '{event:$ev, session_id:.session_id, cwd:.cwd}' "
        "| curl -s -X POST -H 'Content-Type: application/json' -d @- %s >/dev/null 2>&1 || true"
    ) % (json.dumps(event), url)


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
