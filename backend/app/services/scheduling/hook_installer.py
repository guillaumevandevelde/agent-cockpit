"""Install and verify the CC session-hooks in ~/.claude/settings.json.

`settings_hooks_block()` has always generated the correct hook block, but
nothing ever wrote it to disk, so the Notification-hook -> auto-resume
pipeline was dead code in practice. This module makes that installation
additive and idempotent so it can run safely against a settings.json
shared with the user's own interactive sessions.

The status check is tri-state: a hook event is ``installed`` only when the
currently-rendered command matches what's on disk, ``stale`` when an entry
for the event exists but its command diverges from the rendered version
(this is how a ``render_hook_command`` change lands in code without
silently staying broken in the user's settings), and ``missing`` otherwise.

Legacy migration: the hook URL moved from
``/api/v1/scheduled-messages/hook-event`` to
``/api/v1/session-hooks/hook-event`` when the scheduled-messages feature
was retired. Any settings.json entry that still points at the legacy path
is rewritten in place by ``install_missing_hooks`` so the rename stays
silent for the operator (the hook would otherwise 404 with the ``|| true``
sentinel in the command and look like a perfectly fine, never-firing
hook).
"""
import json
import logging

from app.services.scheduling.hook_script import (
    HOOK_EVENT_PATH,
    LEGACY_HOOK_EVENT_MARKER,
    LEGACY_HOOK_EVENT_PATH,
    render_hook_command,
    settings_hooks_block,
)
from app.utils.path_utils import get_claude_user_settings_file

logger = logging.getLogger(__name__)

# Match either the new path (canonical) or the legacy path (about to be
# rewritten). The legacy rewrite is what lets the rename land in code
# without breaking existing installs.
_HOOK_EVENT_MARKER = HOOK_EVENT_PATH
_HOOK_STATUS_MISSING = "missing"
_HOOK_STATUS_STALE = "stale"
_HOOK_STATUS_INSTALLED = "installed"


def _command_is_legacy(command: str) -> bool:
    return isinstance(command, str) and LEGACY_HOOK_EVENT_PATH in command


def _rewrite_legacy_command(command: str, port: int) -> str:
    return command.replace(LEGACY_HOOK_EVENT_PATH, HOOK_EVENT_PATH)


def _event_has_hook_command(event_groups: list | None) -> bool:
    for group in event_groups or []:
        if not isinstance(group, dict):
            continue
        entries = group["hooks"] if "hooks" in group and isinstance(group["hooks"], list) else [group]
        for entry in entries:
            if isinstance(entry, dict) and (
                _HOOK_EVENT_MARKER in str(entry.get("command", ""))
                or LEGACY_HOOK_EVENT_MARKER in str(entry.get("command", ""))
            ):
                return True
    return False


def _installed_command_for_event(event_groups: list | None) -> str | None:
    """Return the first command string for a session-hook event, if any.

    Returns the raw command as written in settings.json (no normalization) so
    we can compare it byte-for-byte with the freshly-rendered command.
    """
    for group in event_groups or []:
        if not isinstance(group, dict):
            continue
        entries = group["hooks"] if "hooks" in group and isinstance(group["hooks"], list) else [group]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            command_str = str(entry.get("command", ""))
            if _HOOK_EVENT_MARKER not in command_str and LEGACY_HOOK_EVENT_MARKER not in command_str:
                continue
            command = entry.get("command")
            if isinstance(command, str):
                return command
    return None


def _rewrite_legacy_hooks(settings: dict, port: int) -> bool:
    """Rewrite every legacy ``/scheduled-messages/hook-event`` command to the
    new path in-place. Returns True if any rewrite happened.

    A settings file rewritten this way still matches the current renderer
    byte-for-byte, so a follow-up ``get_hooks_status`` flips every migrated
    event from ``stale`` to ``installed`` without an extra reinstall step.
    """
    hooks_section = settings.get("hooks")
    if not isinstance(hooks_section, dict):
        return False
    changed = False
    for _event, groups in hooks_section.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, str) and _command_is_legacy(command):
                    entry["command"] = _rewrite_legacy_command(command, port)
                    changed = True
    return changed


def _read_hooks_section() -> dict:
    settings_file = get_claude_user_settings_file()
    if not settings_file.exists():
        return {}
    try:
        return json.loads(settings_file.read_text()).get("hooks", {})
    except (OSError, json.JSONDecodeError):
        logger.warning("could not parse %s while checking scheduling hooks", settings_file)
        return {}


def get_hooks_status(port: int = 8000) -> dict[str, str]:
    """Return per-event hook install status (``missing`` / ``stale`` / ``installed``).

    ``stale`` means the event has a session-hook entry but its command
    differs from what ``render_hook_command(event, port)`` would emit today —
    the symptom of a renderer change that landed in code without a reinstall
    in the user's ``~/.claude/settings.json``.
    """
    hooks_section = _read_hooks_section()
    block = settings_hooks_block(port)
    status: dict[str, str] = {}
    for event in block:
        installed_command = _installed_command_for_event(hooks_section.get(event))
        if installed_command is None:
            status[event] = _HOOK_STATUS_MISSING
        elif installed_command == render_hook_command(event, port):
            status[event] = _HOOK_STATUS_INSTALLED
        else:
            status[event] = _HOOK_STATUS_STALE
    return status


def install_missing_hooks(port: int = 8000) -> dict[str, str]:
    """Additively merge any missing session-hooks into ~/.claude/settings.json.

    Also rewrites any legacy ``/scheduled-messages/hook-event`` entries to
    the new path so the URL rename stays invisible for operators who
    installed before the scheduled-messages feature was retired.

    Only appends entries for events that don't already have a session-hook;
    never touches unrelated hooks or other settings keys. ``stale`` entries
    (a hook is present but its command differs from the current renderer) are
    intentionally left untouched — clearing them is a separate operator step,
    so the returned status reflects the *actual* post-install state of the
    file rather than unconditionally claiming every event is ``installed``.
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

    changed = False
    if _rewrite_legacy_hooks(settings, port):
        changed = True
        logger.info(
            "rewrote legacy scheduled-messages hook URLs to session-hooks path"
        )

    hooks_section = settings.setdefault("hooks", {})
    block = settings_hooks_block(port)

    for event, groups in block.items():
        existing = hooks_section.setdefault(event, [])
        if not _event_has_hook_command(existing):
            existing.extend(groups)
            changed = True

    if changed:
        settings_file.write_text(json.dumps(settings, indent=2))

    return get_hooks_status(port)
