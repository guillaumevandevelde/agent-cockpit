import pytest
from unittest.mock import patch
from app.services.scheduling.delivery import DeliveryEngine
from app.services.scheduling.session_resolver import AMBIGUOUS
from app.services.scheduling.session_registry import SessionRegistry


def _engine(reg):
    return DeliveryEngine(registry=reg)


async def _deliver(eng, **kw):
    base = dict(
        project_dir="/proj", message="go", permission_mode="acceptEdits",
        target_kind="session", target_session_id="s1",
        project_folder="-home-guillaume-proj", resume_settle_s=0,
    )
    base.update(kw)
    return await eng.deliver(**base)


@pytest.mark.asyncio
async def test_alive_idle_session_gets_injected():
    reg = SessionRegistry(); reg.record("Stop", "s1", "/proj", "%3")
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value="win:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await _deliver(eng)
    assert res.outcome == "success"
    assert res.action == "used_existing"
    send.assert_called_once_with("win:0.0", "go")


@pytest.mark.asyncio
async def test_exited_session_is_resume_spawned_then_injected():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=None), \
         patch("app.services.scheduling.delivery.resume_spawn_for", return_value="new:0.0") as spawn, \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await _deliver(eng)
    assert res.outcome == "success"
    assert res.action == "resumed"
    spawn.assert_called_once_with("s1", "-home-guillaume-proj", "/proj", "acceptEdits")
    send.assert_called_once_with("new:0.0", "go")


@pytest.mark.asyncio
async def test_ambiguous_cold_registry_fails():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=AMBIGUOUS), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng)
    assert res.outcome == "failed"
    assert "ambiguous" in (res.error or "").lower()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_alive_but_busy_times_out():
    reg = SessionRegistry(); reg.record("UserPromptSubmit", "s1", "/proj", "%3")
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value="win:0.0"), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng, timeout_s=0.1)
    assert res.outcome == "timeout"
    send.assert_not_called()


@pytest.mark.asyncio
async def test_resume_spawn_failure_marks_failed():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=None), \
         patch("app.services.scheduling.delivery.resume_spawn_for", side_effect=ValueError("boom")), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng)
    assert res.outcome == "failed"
    assert "boom" in (res.error or "")
    send.assert_not_called()
