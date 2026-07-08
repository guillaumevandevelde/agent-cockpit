import json

from app.services.agent_mail import codex_hooks


def _patch_codex_home(monkeypatch, path):
    monkeypatch.setattr(codex_hooks, "get_codex_home", lambda: path)


def test_no_hooks_file_reports_nothing_installed(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)
    assert codex_hooks.installed_codex_hooks() == []


def test_install_writes_session_start_and_prompt_submit(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)

    codex_hooks.install_codex_hooks()

    doc = json.loads((tmp_path / "hooks.json").read_text())
    assert set(doc["hooks"].keys()) == {"SessionStart", "UserPromptSubmit"}
    assert doc["hooks"]["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    command = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "codex_hook_shim.py" in command
    assert "--event" in command and "session-start" in command

    assert codex_hooks.installed_codex_hooks() == ["SessionStart", "UserPromptSubmit"]


def test_install_is_idempotent(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)

    codex_hooks.install_codex_hooks()
    codex_hooks.install_codex_hooks()

    doc = json.loads((tmp_path / "hooks.json").read_text())
    assert len(doc["hooks"]["SessionStart"]) == 1


def test_uninstall_removes_managed_hooks_but_keeps_others(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)
    (tmp_path / "hooks.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/other/script.sh"}]}]},
    }))

    codex_hooks.install_codex_hooks()
    changed = codex_hooks.uninstall_codex_hooks()

    assert changed is True
    doc = json.loads((tmp_path / "hooks.json").read_text())
    remaining = [h["command"] for g in doc["hooks"]["SessionStart"] for h in g["hooks"]]
    assert remaining == ["/other/script.sh"]
