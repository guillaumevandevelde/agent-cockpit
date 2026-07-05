from unittest.mock import AsyncMock, patch

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.services.scheduling.delivery import DeliveryEngine, DeliveryResult
from app.services.scheduling.session_registry import SessionRegistry
from app.services.scheduling.session_resolver import AMBIGUOUS


def _engine(reg):
    return DeliveryEngine(registry=reg)


async def _deliver(eng, **kw):
    base = dict(
        project_dir="/proj", message="go", permission_mode="acceptEdits",
        target_kind="session", target_session_id="s1",
        project_folder="-home-guillaume-proj",
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
         patch("app.services.scheduling.delivery.wait_for_pane_ready",
               new=AsyncMock(return_value=True)) as ready, \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await _deliver(eng)
    assert res.outcome == "success"
    assert res.action == "resumed"
    spawn.assert_called_once_with("s1", "-home-guillaume-proj", "/proj", "acceptEdits")
    ready.assert_awaited_once()  # wait for the resumed TUI before injecting
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


@pytest.mark.asyncio
async def test_crud_passes_session_fields_to_engine():
    from app.models.scheduled_message import ScheduledMessage
    from app.services.scheduling import crud

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        msg = ScheduledMessage(
            target_project="/proj", message="go", trigger_type="once",
            fire_at="2026-01-01T00:00:00", target_kind="session",
            target_session_id="s1", project_folder="-home-guillaume-proj",
        )
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        mid = msg.id

    captured = {}

    async def fake_deliver(**kwargs):
        captured.update(kwargs)
        return DeliveryResult(outcome="success", action="resumed", resolved_session="new:0.0")

    with patch.object(crud._engine, "deliver", side_effect=fake_deliver):
        await crud.run_scheduled_delivery(mid)

    assert captured["target_kind"] == "session"
    assert captured["target_session_id"] == "s1"
    assert captured["project_folder"] == "-home-guillaume-proj"
