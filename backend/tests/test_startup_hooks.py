"""The scheduling hooks must be installed automatically at backend startup --
previously this required a human to visit the Scheduled Messages page and
click "Install hooks", which left the usage-limit auto-resume pipeline dead
on any machine where nobody happened to do that first."""
import json

import pytest

from app.main import ensure_scheduling_hooks_installed
from app.services.scheduling import hook_installer
from app.services.scheduling.hook_script import SCHEDULING_HOOK_EVENTS

ALL_EVENTS = set(SCHEDULING_HOOK_EVENTS)


@pytest.mark.asyncio
async def test_ensure_scheduling_hooks_installed_writes_all_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: settings_file)

    await ensure_scheduling_hooks_installed()

    written = json.loads(settings_file.read_text())
    assert set(written["hooks"]) == ALL_EVENTS


@pytest.mark.asyncio
async def test_ensure_scheduling_hooks_installed_is_idempotent(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: settings_file)

    await ensure_scheduling_hooks_installed()
    await ensure_scheduling_hooks_installed()

    written = json.loads(settings_file.read_text())
    notification_commands = [
        h["command"] for g in written["hooks"]["Notification"] for h in g["hooks"]
    ]
    assert len(notification_commands) == 1


@pytest.mark.asyncio
async def test_ensure_scheduling_hooks_installed_swallows_write_errors(tmp_path, monkeypatch):
    """A shared settings.json some other process is mid-writing to shouldn't
    crash backend startup -- the hooks just stay uninstalled until next restart."""
    def _boom():
        raise OSError("disk full")

    monkeypatch.setattr(hook_installer, "install_missing_hooks", _boom)

    await ensure_scheduling_hooks_installed()
