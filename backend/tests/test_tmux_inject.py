from unittest.mock import patch, call
from app.services.scheduling.tmux_inject import send_text


def test_send_text_runs_send_keys_literal_then_enter():
    with patch("app.services.scheduling.tmux_inject.subprocess.run") as run:
        run.return_value.returncode = 0
        ok = send_text("sess:0.0", "hello world")
    assert ok is True
    assert run.call_args_list[0] == call(
        ["tmux", "send-keys", "-t", "sess:0.0", "-l", "hello world"],
        capture_output=True, text=True, timeout=10,
    )
    assert run.call_args_list[1] == call(
        ["tmux", "send-keys", "-t", "sess:0.0", "Enter"],
        capture_output=True, text=True, timeout=10,
    )


def test_send_text_returns_false_on_failure():
    with patch("app.services.scheduling.tmux_inject.subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "no such session"
        assert send_text("bad", "x") is False
