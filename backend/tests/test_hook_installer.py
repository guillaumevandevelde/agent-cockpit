import json

from app.services.scheduling import hook_installer
from app.services.scheduling.hook_script import SCHEDULING_HOOK_EVENTS

ALL_EVENTS = set(SCHEDULING_HOOK_EVENTS)


def _patch_settings_file(monkeypatch, path):
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: path)


def test_status_all_missing_when_no_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status()

    assert status == {event: "missing" for event in ALL_EVENTS}


def test_install_writes_all_scheduling_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.install_missing_hooks()

    assert status == {event: "installed" for event in ALL_EVENTS}
    written = json.loads(settings_file.read_text())
    assert set(written["hooks"]) == ALL_EVENTS
    for event in ALL_EVENTS:
        commands = [h["command"] for g in written["hooks"][event] for h in g["hooks"]]
        assert any("scheduled-messages/hook-event" in c for c in commands)


def test_install_preserves_unrelated_settings_and_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "/some/other/cache-heal.mjs"}]}
            ]
        },
        "theme": "dark",
    }))
    _patch_settings_file(monkeypatch, settings_file)

    hook_installer.install_missing_hooks()

    written = json.loads(settings_file.read_text())
    assert written["theme"] == "dark"
    session_start_commands = [
        h["command"] for g in written["hooks"]["SessionStart"] for h in g["hooks"]
    ]
    assert "/some/other/cache-heal.mjs" in session_start_commands
    assert any("scheduled-messages/hook-event" in c for c in session_start_commands)


def test_install_is_idempotent(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    hook_installer.install_missing_hooks()
    hook_installer.install_missing_hooks()

    written = json.loads(settings_file.read_text())
    notification_commands = [
        h["command"] for g in written["hooks"]["Notification"] for h in g["hooks"]
    ]
    assert len(notification_commands) == 1


def test_status_reflects_partial_install(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {
            "Notification": [
                {"hooks": [{
                    "type": "command",
                    "command": "curl -s -X POST http://localhost:8000/api/v1/scheduled-messages/hook-event",
                }]}
            ]
        }
    }))
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status()

    assert status["Notification"] == "stale"
    assert status["Stop"] == "missing"
    assert status["UserPromptSubmit"] == "missing"
    assert status["SessionStart"] == "missing"


def test_install_only_adds_missing_events(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {
            "Notification": [
                {"hooks": [{
                    "type": "command",
                    "command": "curl -s -X POST http://localhost:8000/api/v1/scheduled-messages/hook-event",
                }]}
            ]
        }
    }))
    _patch_settings_file(monkeypatch, settings_file)

    hook_installer.install_missing_hooks()

    written = json.loads(settings_file.read_text())
    notification_groups = written["hooks"]["Notification"]
    assert len(notification_groups) == 1
    assert len(notification_groups[0]["hooks"]) == 1


def test_status_reports_installed_when_command_matches_renderer(tmp_path, monkeypatch):
    """Tri-state: the exact rendered command from settings_hooks_block() is
    reported as ``installed`` — neither ``stale`` (byte-drift from the
    renderer) nor ``missing`` (no entry at all)."""
    from app.services.scheduling import hook_script

    settings_file = tmp_path / "settings.json"
    rendered = hook_script.settings_hooks_block(port=8000)
    settings_file.write_text(json.dumps({
        "hooks": {
            event: groups
            for event, groups in rendered.items()
        }
    }))
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status(port=8000)

    assert status == {event: "installed" for event in ALL_EVENTS}


def test_status_reports_stale_when_command_diverges_from_renderer(tmp_path, monkeypatch):
    """Tri-state: a present-but-divergent command is ``stale`` (e.g. the
    renderer added ``notification_type`` but the on-disk command predates
    that change)."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {
            "Notification": [
                {"hooks": [{
                    "type": "command",
                    # Pre-CC-2.1.198 shape: no notification_type forward.
                    "command": (
                        "jq -c --arg ev Notification "
                        "'{event:$ev, session_id:.session_id, cwd:.cwd, "
                        "tmux_pane:env.TMUX_PANE, message:.message}' "
                        "| curl -s -X POST -H 'Content-Type: application/json' "
                        "-d @- http://localhost:8000/api/v1/scheduled-messages/hook-event"
                        " >/dev/null 2>&1 || true"
                    ),
                }]}
            ]
        }
    }))
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status(port=8000)

    assert status["Notification"] == "stale"
    # The other events never had any entry at all.
    assert all(status[event] == "missing" for event in ALL_EVENTS - {"Notification"})


def test_status_reports_missing_when_event_has_no_entry(tmp_path, monkeypatch):
    """Tri-state: an event with no entry in settings.json is ``missing``."""
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status()

    assert status == {event: "missing" for event in ALL_EVENTS}


def test_install_returns_actual_post_install_status(tmp_path, monkeypatch):
    """install_missing_hooks must report the *current* disk state, not
    unconditionally claim every event is installed: a stale entry is not
    refreshed by this call, so the returned status for that event stays
    ``stale``. The router reads this status to drive its aggregate
    ``installed`` flag — lying here would surface a false success in the
    UI and hide the warning."""
    settings_file = tmp_path / "settings.json"
    # Seed every event with a divergent (stale) command.
    settings_file.write_text(json.dumps({
        "hooks": {
            event: [{"hooks": [{
                "type": "command",
                "command": "curl -s -X POST http://localhost:8000/api/v1/scheduled-messages/hook-event",
            }]}]
            for event in ALL_EVENTS
        }
    }))
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.install_missing_hooks()

    # Nothing changed on disk: every event is still stale.
    assert all(value == "stale" for value in status.values())
    written = json.loads(settings_file.read_text())
    for event in ALL_EVENTS:
        assert len(written["hooks"][event]) == 1
        assert written["hooks"][event][0]["hooks"][0]["command"].startswith("curl -s -X POST")
