"""Install and verify the Agent Mail lifecycle hooks in ~/.claude/settings.json.

Additive, idempotent merge — mirrors app.services.scheduling.hook_installer
exactly, but for Agent Mail's 4 events and URL marker.
"""
import json
import logging

from app.services.agent_mail.hook_script import settings_hooks_block
from app.utils.path_utils import get_claude_user_settings_file

logger = logging.getLogger(__name__)

_HOOK_EVENT_MARKER = "agent-mail/hooks/"


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
        logger.warning("could not parse %s while checking agent mail hooks", settings_file)
        return {}


def get_hooks_status(port: int = 8000) -> dict[str, bool]:
    """Return which of the four Agent Mail hook events are already installed."""
    hooks_section = _read_hooks_section()
    return {event: _event_has_hook_command(hooks_section.get(event)) for event in settings_hooks_block(port)}


def install_missing_hooks(port: int = 8000) -> dict[str, bool]:
    """Additively merge any missing Agent Mail hooks into ~/.claude/settings.json."""
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
