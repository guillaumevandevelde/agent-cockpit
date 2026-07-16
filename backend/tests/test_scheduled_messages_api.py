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
async def test_hook_event_session_end_frees_session_registry_slot():
    """The endpoint that feeds session_registry.record() must accept
    SessionEnd (it's rejected by the HookEvent schema until this card) and
    release the slot immediately, rather than waiting for the next tmux
    reconciliation sweep."""
    from app.services.scheduling.session_registry import session_registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "SessionStart", "session_id": "sEnd",
                                "cwd": "/proj", "tmux_pane": "%8"})
        assert r.status_code == 200
        assert session_registry.pane_for("sEnd") == "%8"

        r = await ac.post("/api/v1/scheduled-messages/hook-event",
                          json={"event": "SessionEnd", "session_id": "sEnd",
                                "cwd": "/proj", "tmux_pane": "%8"})
        assert r.status_code == 200
    assert session_registry.pane_for("sEnd") is None


@pytest.mark.asyncio
async def test_hook_event_limit_notification_moves_kanban_card_to_resume():
    """A "hit your session limit" Notification triggers the kanban To-Resume move,
    independent of whether the scheduled-messages auto-resume toggle is on. The
    parsed reset time is passed through as scheduled_at so the card's own
    _is_due check (not just the global dispatch pause) knows when it's eligible
    again."""
    from unittest import mock

    import app.kanban.dispatch as dispatch
    from app.services.scheduling.auto_resume import auto_resume_service

    message = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    expected_reset_time, _tz = auto_resume_service.parse_reset_time(message)

    transport = ASGITransport(app=app)
    with mock.patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={"event": "Notification", "session_id": "s2",
                      "cwd": "/p/.claude/worktrees/k-limit-0001",
                      "message": message},
            )
            assert r.status_code == 200

    move_mock.assert_awaited_once_with(
        "/p/.claude/worktrees/k-limit-0001",
        scheduled_at=expected_reset_time.isoformat(),
    )


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
async def test_hook_event_limit_notification_pauses_only_affected_provider(monkeypatch):
    """A limit hit on a minimax column pauses ONLY minimax: anthropic traffic
    (and the legacy global slot) stays clear so a provider-wide outage does
    not freeze unrelated subscriptions. Backed by a real DB row + the same
    provider-resolution chain dispatch_project uses at spawn time."""
    from unittest import mock

    import app.kanban.db as kdb
    import app.kanban.dispatch as dispatch
    from app.kanban import dispatch_pause, service
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal
    PK = "git:example.com/limit-test/repo"
    KanbanSessionLocal = TestSessionLocal()
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        # Engineer column defaults to minimax (the subscription that hit 429).
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "rl-card", "column": "engineer"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-rl-prov-0001"},
        )
        await s.commit()

    # Patch the kanban DB the hook talks to so it sees our test card. The
    # cwd's project path is a stub -- the hook only uses it via
    # _provider_for_cwd -> safe_resolve_project_key, which we monkeypatch too.
    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda path: PK,
    )

    transport = ASGITransport(app=app)
    # Skip the actual move (no project_path fixtures here) and the
    # associated kill: the per-provider pause is what we're asserting on, and
    # the move/cancel paths are already covered in test_kanban_dispatch.
    with mock.patch.object(dispatch, "move_limited_session_to_resume", return_value=True):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-rl-prov",
                    "cwd": "/proj/.claude/worktrees/k-rl-prov-0001",
                    "message": "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
                },
            )
            assert r.status_code == 200, r.text

    async with KanbanSessionLocal() as s:
        # Minimax is paused (the only slot the hook writes).
        assert await dispatch_pause.is_dispatch_paused(s, provider="minimax") is True
        minimax_until = await dispatch_pause.get_paused_until(s, provider="minimax")
        assert minimax_until is not None
        # Anthropic + bedrock slots are untouched.
        assert await dispatch_pause.is_dispatch_paused(s, provider="anthropic") is False
        assert await dispatch_pause.is_dispatch_paused(s, provider="bedrock") is False
        # Legacy global slot is also untouched (no regression -- today's
        # behaviour is "minimax slot set", which is *strictly more
        # targeted*, not a regression).
        assert await dispatch_pause.is_dispatch_paused(s) is False


@pytest.mark.asyncio
async def test_hook_event_limit_notification_falls_back_to_global_pause_when_no_card(monkeypatch):
    """When no kanban card can be matched to the cwd (e.g. a manual session or
    a non-worktree directory), the hook still pauses -- the legacy global
    slot, not a per-provider one -- so behaviour outside kanban
    dispatches stays identical to before."""
    from unittest import mock

    import app.kanban.db as kdb
    import app.kanban.dispatch as dispatch
    from app.kanban import dispatch_pause
    from tests.kanban_test_db import TestSessionLocal
    PK = "git:example.com/nocard-test/repo"
    KanbanSessionLocal = TestSessionLocal()
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda path: PK,
    )

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume", return_value=False):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-rl-nomatch",
                    "cwd": "/proj/.claude/worktrees/k-rl-nomatch",
                    "message": "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
                },
            )
            assert r.status_code == 200, r.text

    async with KanbanSessionLocal() as s:
        # Legacy global slot: active.
        assert await dispatch_pause.is_dispatch_paused(s) is True
        # No per-provider slots touched.
        assert await dispatch_pause.get_paused_until(s, provider="minimax") is None
        assert await dispatch_pause.get_paused_until(s, provider="anthropic") is None
        assert await dispatch_pause.get_paused_until(s, provider="bedrock") is None


@pytest.mark.asyncio
async def test_hook_event_agent_needs_input_posts_card_activity_comment():
    """CC 2.1.198+ `agent_needs_input` notifications must surface as a
    kanban activity comment so the operator can see "agent waiting" on
    the card. The card is NOT auto-moved."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume") as move_mock, \
         mock.patch.object(
             dispatch, "post_agent_status_comment", return_value=True,
         ) as post_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-needs-1",
                    "cwd": "/p/.claude/worktrees/k-needs-0001",
                    "notification_type": "agent_needs_input",
                    "message": "background agent needs your input",
                },
            )
            assert r.status_code == 200

    # No-op for the rate-limit branch (different notification kind) and
    # the comment path was hit with the canonical "waiting for input" text.
    move_mock.assert_not_called()
    post_mock.assert_awaited_once()
    args, _ = post_mock.await_args
    assert args[0] == "/p/.claude/worktrees/k-needs-0001"
    assert "waiting for input" in args[1].lower()


@pytest.mark.asyncio
async def test_hook_event_agent_completed_posts_card_activity_comment():
    """CC 2.1.198+ `agent_completed` notifications must surface as a
    kanban activity comment so the operator sees "agent finished" on the
    card. The card is NOT auto-moved (Done stays a human/engineer action)."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume") as move_mock, \
         mock.patch.object(
             dispatch, "post_agent_status_comment", return_value=True,
         ) as post_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-done-1",
                    "cwd": "/p/.claude/worktrees/k-done-0001",
                    "notification_type": "agent_completed",
                    "message": "background agent finished",
                },
            )
            assert r.status_code == 200

    move_mock.assert_not_called()
    post_mock.assert_awaited_once()
    args, _ = post_mock.await_args
    assert args[0] == "/p/.claude/worktrees/k-done-0001"
    assert "reported completion" in args[1].lower()


@pytest.mark.asyncio
async def test_hook_event_agent_completed_does_not_move_card_to_done():
    """Even though `agent_completed` reports the agent finished, the
    card must NOT be auto-moved to Done — matches the rate-limit design
    where only the explicit `move_limited_session_to_resume` path moves
    a card, and only on a real rate-limit hit. Surfacing the event as a
    comment is the right level of reaction; the engineer still flips
    the card to Done after the wrap-up work."""
    from unittest import mock

    import app.kanban.dispatch as dispatch
    from app.kanban.operations import apply_operation

    transport = ASGITransport(app=app)
    # Spy on `move` ops against the kanban op-log. The new branch must not
    # produce any; the rate-limit branch uses `_move_to_resume`, which does,
    # so the absence of a `move` op is the strongest guarantee we can give
    # from this hook alone (no card was moved to Done or anywhere else).
    move_ops: list[tuple] = []
    original_apply = apply_operation

    async def spy_apply(session, *, op_type, entity_type, project_key,
                        entity_id, payload):
        if op_type == "move":
            move_ops.append((entity_type, entity_id, payload))
        return await original_apply(
            session, op_type=op_type, entity_type=entity_type,
            project_key=project_key, entity_id=entity_id, payload=payload,
        )

    with mock.patch.object(dispatch, "move_limited_session_to_resume") as move_mock, \
         mock.patch.object(dispatch, "post_agent_status_comment", return_value=True), \
         mock.patch("app.kanban.operations.apply_operation", side_effect=spy_apply):
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-done-2",
                    "cwd": "/p/.claude/worktrees/k-done-0002",
                    "notification_type": "agent_completed",
                    "message": "background agent finished",
                },
            )
            assert r.status_code == 200

    move_mock.assert_not_called()
    assert move_ops == [], (
        f"agent_completed must not produce any `move` ops; saw: {move_ops}"
    )


@pytest.mark.asyncio
async def test_hook_event_needs_input_via_message_substring_fallback():
    """Pre-2.1.198 hook scripts don't forward notification_type; the
    router must still classify the canonical '<label> needs your input'
    message via the substring fallback and post the same activity comment."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(
        dispatch, "post_agent_status_comment", return_value=True,
    ) as post_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-fb-1",
                    "cwd": "/p/.claude/worktrees/k-fb-0001",
                    "message": "background agent needs your input",
                },
            )
            assert r.status_code == 200

    post_mock.assert_awaited_once()
    args, _ = post_mock.await_args
    assert "waiting for input" in args[1].lower()


@pytest.mark.asyncio
async def test_hook_event_other_notification_types_are_silently_dropped():
    """permission_prompt / idle_prompt / auth_success / elicitation_*
    must NOT trigger the agent-status comment branch — they're
    unrelated to background-agent state and just create noise if
    commented on every card."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    for ntype in ("permission_prompt", "idle_prompt", "auth_success",
                  "elicitation_dialog", "elicitation_complete", "elicitation_response"):
        with mock.patch.object(
            dispatch, "move_limited_session_to_resume",
        ) as move_mock, mock.patch.object(
            dispatch, "post_agent_status_comment",
        ) as post_mock:
            async with AsyncClient(transport=transport, base_url="http://t") as ac:
                r = await ac.post(
                    "/api/v1/scheduled-messages/hook-event",
                    json={
                        "event": "Notification", "session_id": f"s-{ntype}",
                        "cwd": f"/p/.claude/worktrees/k-{ntype}-0001",
                        "notification_type": ntype,
                        "message": "Claude needs your input",
                    },
                )
                assert r.status_code == 200
        move_mock.assert_not_called()
        post_mock.assert_not_called()


@pytest.mark.asyncio
async def test_hook_event_notification_without_type_or_relevant_message_is_dropped():
    """A Notification with no notification_type and no recognisable message
    (e.g. a future Claude Code variant) must not falsely fire the
    new-comment branch. Same shape as the pre-existing 'other notifications
    do not touch kanban' test, but for clarity kept as a separate test
    so the new code path is explicitly covered."""
    from unittest import mock

    import app.kanban.dispatch as dispatch

    transport = ASGITransport(app=app)
    with mock.patch.object(dispatch, "move_limited_session_to_resume") as move_mock, \
         mock.patch.object(dispatch, "post_agent_status_comment") as post_mock:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                "/api/v1/scheduled-messages/hook-event",
                json={
                    "event": "Notification", "session_id": "s-other",
                    "cwd": "/p/.claude/worktrees/k-other-0002",
                    "message": "Waiting for your input",
                },
            )
            assert r.status_code == 200

    move_mock.assert_not_called()
    post_mock.assert_not_called()


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
