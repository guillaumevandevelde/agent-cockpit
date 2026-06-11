from app.services.scheduling.hook_script import render_hook_command, settings_hooks_block


def test_hook_command_posts_event():
    cmd = render_hook_command(event="Stop", port=8000)
    assert "curl" in cmd
    assert "Stop" in cmd
    assert "hook-event" in cmd
    assert "session_id" in cmd


def test_settings_block_has_all_events():
    block = settings_hooks_block(port=8000)
    assert set(block) == {"UserPromptSubmit", "Stop", "Notification", "SessionStart"}
