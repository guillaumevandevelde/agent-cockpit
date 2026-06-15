import pytest
from unittest.mock import patch, AsyncMock
from app.services.scheduling.delivery import DeliveryEngine
from app.services.scheduling.idle_state import IdleState


def _engine(idle):
    return DeliveryEngine(idle_state=idle)


@pytest.mark.asyncio
async def test_existing_idle_session_gets_message():
    idle = IdleState(); idle.record("Stop", "/proj", "s1")
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value="t:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="hi", permission_mode="acceptEdits")
    assert res.outcome == "success"
    assert res.action == "used_existing"
    send.assert_called_once_with("t:0.0", "hi")


@pytest.mark.asyncio
async def test_no_session_spawns_waits_for_ready_then_sends():
    idle = IdleState()
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value=None), \
         patch("app.services.scheduling.delivery.spawn_for", return_value="new:0.0") as spawn, \
         patch("app.services.scheduling.delivery.wait_for_pane_ready",
               new=AsyncMock(return_value=True)) as ready, \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="go", permission_mode="bypass")
    assert res.action == "spawned"
    assert res.outcome == "success"
    spawn.assert_called_once()
    ready.assert_awaited_once()  # never trust stale cwd-idle for a fresh spawn
    send.assert_called_once_with("new:0.0", "go")


@pytest.mark.asyncio
async def test_spawned_session_not_ready_times_out_without_sending():
    idle = IdleState()
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value=None), \
         patch("app.services.scheduling.delivery.spawn_for", return_value="new:0.0"), \
         patch("app.services.scheduling.delivery.wait_for_pane_ready",
               new=AsyncMock(return_value=False)), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="go", permission_mode="bypass")
    assert res.outcome == "timeout"
    assert res.action == "spawned"
    send.assert_not_called()  # don't fire keystrokes into a not-yet-ready pane


@pytest.mark.asyncio
async def test_no_session_skip_fails():
    idle = IdleState()
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value=None):
        res = await eng.deliver(project_dir="/proj", message="x",
                                on_missing_session="skip")
    assert res.outcome == "failed"


@pytest.mark.asyncio
async def test_busy_then_timeout_marks_timeout():
    idle = IdleState(); idle.record("UserPromptSubmit", "/proj", "s1")
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value="t:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="hi",
                                permission_mode="acceptEdits", timeout_s=0.1)
    assert res.outcome == "timeout"
    send.assert_not_called()
