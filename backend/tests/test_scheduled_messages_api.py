import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_create_list_delete_once():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "hi",
                   "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]

        r = await ac.get("/api/v1/scheduled-messages")
        assert any(m["id"] == mid for m in r.json()["items"])

        r = await ac.delete(f"/api/v1/scheduled-messages/{mid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_create_cron_and_attempts_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "daily",
                   "trigger_type": "cron", "cron_expr": "0 9 * * 1-5"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = await ac.get(f"/api/v1/scheduled-messages/{mid}/attempts")
        assert r.status_code == 200
        assert r.json() == []
        await ac.delete(f"/api/v1/scheduled-messages/{mid}")


@pytest.mark.asyncio
async def test_delete_history_removes_terminal_messages():
    from sqlalchemy import update

    from app.database import AsyncSessionLocal
    from app.models.scheduled_message import ScheduledMessage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        once_payload = {"target_project": "/tmp", "message": "x",
                        "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r1 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        r2 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        r3 = await ac.post("/api/v1/scheduled-messages", json=once_payload)
        id_delivered = r1.json()["id"]
        id_failed = r2.json()["id"]
        id_scheduled = r3.json()["id"]

    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ScheduledMessage)
            .where(ScheduledMessage.id == id_delivered)
            .values(status="delivered")
        )
        await s.execute(
            update(ScheduledMessage)
            .where(ScheduledMessage.id == id_failed)
            .values(status="failed")
        )
        await s.commit()

    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/scheduled-messages/history")
        assert r.status_code == 200
        assert r.json()["deleted"] >= 2

        remaining = (await ac.get("/api/v1/scheduled-messages")).json()["items"]
        remaining_ids = [m["id"] for m in remaining]
        assert id_scheduled in remaining_ids
        assert id_delivered not in remaining_ids
        assert id_failed not in remaining_ids

        await ac.delete(f"/api/v1/scheduled-messages/{id_scheduled}")


@pytest.mark.asyncio
async def test_delete_history_when_nothing_to_clean():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/scheduled-messages/history")
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


@pytest.mark.asyncio
async def test_bulk_delete_removes_selected_messages():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "x",
                   "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r1 = await ac.post("/api/v1/scheduled-messages", json=payload)
        r2 = await ac.post("/api/v1/scheduled-messages", json=payload)
        r3 = await ac.post("/api/v1/scheduled-messages", json=payload)
        id1, id2, id3 = r1.json()["id"], r2.json()["id"], r3.json()["id"]

        r = await ac.post("/api/v1/scheduled-messages/bulk-delete", json={"ids": [id1, id2]})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] == 2

        remaining_ids = [m["id"] for m in (await ac.get("/api/v1/scheduled-messages")).json()["items"]]
        assert id1 not in remaining_ids
        assert id2 not in remaining_ids
        assert id3 in remaining_ids

        await ac.delete(f"/api/v1/scheduled-messages/{id3}")


@pytest.mark.asyncio
async def test_bulk_delete_ignores_unknown_ids():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/bulk-delete", json={"ids": [999999]})
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


@pytest.mark.asyncio
async def test_bulk_delete_with_empty_ids_is_noop():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/bulk-delete", json={"ids": []})
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


@pytest.mark.asyncio
async def test_bulk_delete_unregisters_from_scheduler():
    from unittest import mock

    from app.services.scheduling.scheduler import scheduler_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "cron-me",
                   "trigger_type": "cron", "cron_expr": "0 9 * * 1-5"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        mid = r.json()["id"]

        with mock.patch.object(scheduler_service, "remove") as remove_mock:
            r = await ac.post("/api/v1/scheduled-messages/bulk-delete", json={"ids": [mid]})
            assert r.status_code == 200
            assert r.json()["deleted"] == 1
        remove_mock.assert_called_once_with(mid)

        remaining_ids = [m["id"] for m in (await ac.get("/api/v1/scheduled-messages")).json()["items"]]
        assert mid not in remaining_ids


@pytest.mark.asyncio
async def test_hook_event_updates_idle_state():
    from app.services.scheduling.idle_state import idle_state
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "Stop", "session_id": "s1", "cwd": "/tmp/idletest"})
        assert r.status_code == 200
    assert idle_state.is_idle("/tmp/idletest") is True


@pytest.mark.asyncio
async def test_hook_event_populates_session_registry():
    from app.services.scheduling.session_registry import session_registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "SessionStart", "session_id": "sX",
                                "cwd": "/proj", "tmux_pane": "%7"})
        assert r.status_code == 200
    assert session_registry.pane_for("sX") == "%7"


@pytest.mark.asyncio
async def test_hook_event_limit_notification_moves_kanban_card_to_resume():
    """A "hit your session limit" Notification triggers the kanban To-Resume move,
    independent of whether the scheduled-messages auto-resume toggle is on."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={"event": "Notification", "session_id": "s2",
                      "cwd": "/p/.claude/worktrees/k-limit-0001",
                      "message": "You've hit your session limit · resets 11:10pm (Europe/Brussels)"},
            )
            assert r.status_code == 200

    move_mock.assert_awaited_once_with("/p/.claude/worktrees/k-limit-0001")


@pytest.mark.asyncio
async def test_hook_event_non_limit_notification_does_not_touch_kanban():
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume") as move_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={"event": "Notification", "session_id": "s3",
                      "cwd": "/p/.claude/worktrees/k-other-0001",
                      "message": "Waiting for your input"},
            )
            assert r.status_code == 200

    move_mock.assert_not_called()


@pytest.mark.asyncio
async def test_hook_event_limit_notification_pauses_global_dispatch():
    """The usage limit is account-wide: every session hits the same wall for the
    rest of the reset window, so a limit notification must pause the whole
    auto-dispatch tick (every project), not just move the reporting card."""
    from unittest import mock

    import app.kanban.dispatch as dispatch
    from app.kanban import dispatch_pause
    from app.kanban.db import KanbanSessionLocal

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume", return_value=False):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={"event": "Notification", "session_id": "s4",
                      "cwd": "/p/.claude/worktrees/k-limit-0002",
                      "message": "You've hit your session limit · resets 11:10pm (Europe/Brussels)"},
            )
            assert r.status_code == 200

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is True


@pytest.mark.asyncio
async def test_hook_event_limit_notification_without_reset_time_falls_back_to_conservative_pause():
    """If the reset time can't be parsed from the message (e.g. a weekly/model
    cap with different wording), we still must not skip the pause -- guessing a
    conservative fallback beats leaving dispatch running to immediately re-hit
    the same account-wide wall."""
    from datetime import UTC, datetime, timedelta
    from unittest import mock

    import app.kanban.dispatch as dispatch
    from app.kanban import dispatch_pause
    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume", return_value=False):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={"event": "Notification", "session_id": "s5",
                      "cwd": "/p/.claude/worktrees/k-limit-0003",
                      "message": "You've hit your session limit"},
            )
            assert r.status_code == 200

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is True
        paused_until = await dispatch_pause.get_paused_until(s)
        assert paused_until is not None
        expected = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
        assert abs((paused_until - expected).total_seconds()) < 30


@pytest.mark.asyncio
async def test_hooks_status_and_install_roundtrip(tmp_path, monkeypatch):
    """The hooks-status/hooks-install endpoints drive hook_installer directly."""
    from app.services.scheduling import hook_installer

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: settings_file)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/scheduled-messages/hooks-status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["installed"] is False
        assert body["events"]["Notification"] is False

        r = await ac.post("/api/v1/scheduled-messages/hooks-install")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["installed"] is True
        assert all(body["events"].values())

        r = await ac.get("/api/v1/scheduled-messages/hooks-status")
        assert r.json()["installed"] is True
