from unittest.mock import patch

from app.services.scheduling.session_resolver import permission_flags, resolve_target, spawn_for


def test_permission_flags_mapping():
    assert permission_flags("default") == []
    assert permission_flags("acceptEdits") == ["--permission-mode", "acceptEdits"]
    assert permission_flags("bypass") == ["--dangerously-skip-permissions"]


def test_resolve_target_picks_matching_project():
    sessions = [
        {"tmux_target": "a:0.0", "cwd": "/home/g/dev/x"},
        {"tmux_target": "b:0.0", "cwd": "/home/g/dev/y"},
    ]
    with patch("app.services.scheduling.session_resolver.discover_agent_sessions", return_value=sessions):
        assert resolve_target("/home/g/dev/y") == "b:0.0"


def test_resolve_target_returns_none_when_absent():
    with patch("app.services.scheduling.session_resolver.discover_agent_sessions", return_value=[]):
        assert resolve_target("/home/g/dev/z") is None


def test_spawn_for_passes_permission_flags():
    with patch("app.services.scheduling.session_resolver.spawn_session",
               return_value={"tmux_target": "new:0.0"}) as sp:
        target = spawn_for("/tmp", "acceptEdits")
    assert target == "new:0.0"
    _, kwargs = sp.call_args
    assert kwargs["extra_args"] == ["--permission-mode", "acceptEdits"]
