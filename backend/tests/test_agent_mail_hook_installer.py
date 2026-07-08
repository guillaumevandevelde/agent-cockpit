import json

from app.services.agent_mail import hook_installer

ALL_EVENTS = {"SessionStart", "UserPromptSubmit", "SessionEnd", "PostToolUse"}


def _patch_settings_file(monkeypatch, path):
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: path)


def test_status_all_missing_when_no_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    assert hook_installer.get_hooks_status() == {event: False for event in ALL_EVENTS}


def test_install_writes_all_four_hooks_with_post_tool_use_matcher(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.install_missing_hooks()

    assert status == {event: True for event in ALL_EVENTS}
    written = json.loads(settings_file.read_text())
    assert set(written["hooks"]) == ALL_EVENTS
    for event in ALL_EVENTS:
        commands = [h["command"] for g in written["hooks"][event] for h in g["hooks"]]
        assert any("agent-mail/hooks/" in c for c in commands)
    post_tool_use_group = written["hooks"]["PostToolUse"][0]
    assert post_tool_use_group["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert "matcher" not in written["hooks"]["SessionStart"][0]


def test_install_is_idempotent_and_preserves_unrelated_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/some/other.sh"}]}]},
    }))
    _patch_settings_file(monkeypatch, settings_file)

    hook_installer.install_missing_hooks()
    hook_installer.install_missing_hooks()

    written = json.loads(settings_file.read_text())
    session_start_commands = [h["command"] for g in written["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "/some/other.sh" in session_start_commands
    agent_mail_commands = [c for c in session_start_commands if "agent-mail/hooks/" in c]
    assert len(agent_mail_commands) == 1
