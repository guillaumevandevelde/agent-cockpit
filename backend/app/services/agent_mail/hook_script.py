"""Render the CC hook commands that POST Agent Mail lifecycle events to the
backend and print the response (Claude Code injects a command hook's stdout
as hookSpecificOutput.additionalContext when it parses as the expected JSON
shape). Mirrors app.services.scheduling.hook_script's structure."""

POST_TOOL_USE_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"

MAIL_HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "user-prompt-submit",
    "SessionEnd": "session-end",
    "PostToolUse": "post-tool-use",
}


def render_hook_command(slug: str, port: int = 8000) -> str:
    url = f"http://127.0.0.1:{port}/api/v1/agent-mail/hooks/{slug}"
    return (
        f"curl -s -f --connect-timeout 0.25 -m 1 -X POST {url} "
        "-H 'Content-Type: application/json' --data-binary @- 2>/dev/null || true"
    )


def settings_hooks_block(port: int = 8000) -> dict:
    """Return a dict to merge into ~/.claude/settings.json 'hooks'."""
    def entry(event: str, slug: str):
        group: dict = {"hooks": [{"type": "command", "command": render_hook_command(slug, port)}]}
        if event == "PostToolUse":
            group["matcher"] = POST_TOOL_USE_MATCHER
        return [group]
    return {event: entry(event, slug) for event, slug in MAIL_HOOK_EVENTS.items()}
