import json

from app.services.scheduling import hook_installer

ALL_EVENTS = {"UserPromptSubmit", "Stop", "Notification", "SessionStart"}


def _patch_settings_file(monkeypatch, path):
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: path)


def test_status_all_missing_when_no_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.get_hooks_status()

    assert status == {event: False for event in ALL_EVENTS}


def test_install_writes_all_four_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.install_missing_hooks()

    assert status == {event: True for event in ALL_EVENTS}
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

    assert status["Notification"] is True
    assert status["Stop"] is False
    assert status["UserPromptSubmit"] is False
    assert status["SessionStart"] is False


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
