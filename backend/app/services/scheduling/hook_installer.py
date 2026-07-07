"""Install and verify the CC scheduling hooks in ~/.claude/settings.json.

`settings_hooks_block()` has always generated the correct hook block, but
nothing ever wrote it to disk (see docs/cockpit/analyse-sessie-limieten-claude-code.md),
so the Notification-hook -> auto-resume pipeline was dead code in practice.
This module makes that installation additive and idempotent so it can run
safely against a settings.json shared with the user's own interactive sessions.
"""
import json
import logging

from app.services.scheduling.hook_script import settings_hooks_block
from app.utils.path_utils import get_claude_user_settings_file

logger = logging.getLogger(__name__)

_HOOK_EVENT_MARKER = "scheduled-messages/hook-event"


def _event_has_hook_command(event_groups: list | None) -> bool:
    for group in event_groups or []:
        if not isinstance(group, dict):
            continue
        entries = group["hooks"] if "hooks" in group and isinstance(group["hooks"], list) else [group]
        for entry in entries:
            if isinstance(entry, dict) and _HOOK_EVENT_MARKER in str(entry.get("command", "")):
                return True
    return False


def _read_hooks_section() -> dict:
    settings_file = get_claude_user_settings_file()
    if not settings_file.exists():
        return {}
    try:
        return json.loads(settings_file.read_text()).get("hooks", {})
    except (OSError, json.JSONDecodeError):
        logger.warning("could not parse %s while checking scheduling hooks", settings_file)
        return {}


def get_hooks_status(port: int = 8000) -> dict[str, bool]:
    """Return which of the four scheduling hook events are already installed."""
    hooks_section = _read_hooks_section()
    return {
        event: _event_has_hook_command(hooks_section.get(event))
        for event in settings_hooks_block(port)
    }


def install_missing_hooks(port: int = 8000) -> dict[str, bool]:
    """Additively merge any missing scheduling hooks into ~/.claude/settings.json.

    Only appends entries for events that don't already have a scheduling hook;
    never touches unrelated hooks or other settings keys.
    """
    settings_file = get_claude_user_settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (OSError, json.JSONDecodeError):
            settings = {}
    else:
        settings = {}

    hooks_section = settings.setdefault("hooks", {})
    block = settings_hooks_block(port)
    changed = False

    for event, groups in block.items():
        existing = hooks_section.setdefault(event, [])
        if not _event_has_hook_command(existing):
            existing.extend(groups)
            changed = True

    if changed:
        settings_file.write_text(json.dumps(settings, indent=2))

    return {event: True for event in block}
