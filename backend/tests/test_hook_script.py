from app.services.scheduling.hook_script import render_hook_command, settings_hooks_block
from app.models.scheduled_message_schemas import HookEvent


def test_hook_command_posts_event():
    cmd = render_hook_command(event="Stop", port=8000)
    assert "curl" in cmd
    assert "Stop" in cmd
    assert "hook-event" in cmd
    assert "session_id" in cmd


def test_settings_block_has_all_events():
    block = settings_hooks_block(port=8000)
    assert set(block) == {"UserPromptSubmit", "Stop", "Notification", "SessionStart"}


def test_render_includes_tmux_pane():
    cmd = render_hook_command("Stop", port=8000)
    assert "env.TMUX_PANE" in cmd
    assert "tmux_pane" in cmd


def test_hook_event_accepts_optional_pane():
    ev = HookEvent(event="Stop", session_id="s1", cwd="/proj")
    assert ev.tmux_pane is None
    ev2 = HookEvent(event="Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert ev2.tmux_pane == "%3"
