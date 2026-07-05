import pytest

from app.services.scheduling.session_registry import SessionRegistry


def test_pane_mapping_and_idle_transitions():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.pane_for("s1") == "%3"
    assert reg.is_idle("s1") is False          # SessionStart => busy
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is True
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is False


def test_pane_kept_when_event_has_no_pane():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane=None)
    assert reg.pane_for("s1") == "%3"


def test_unknown_session_is_not_idle():
    reg = SessionRegistry()
    assert reg.is_idle("nope") is False
    assert reg.pane_for("nope") is None


def test_external_reservations_count_toward_session_total():
    # Sandcastle runs have no tmux pane but still consume memory, so they must
    # count against the shared session budget.
    reg = SessionRegistry(max_sessions=3)
    assert reg.session_count == 0
    reg.reserve_external("k-a-0001")
    reg.reserve_external("k-b-0002")
    assert reg.session_count == 2
    # Mixed with a tmux session:
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%1")
    assert reg.session_count == 3
    assert reg.can_add_session() is False


def test_external_reservations_are_released_and_idempotent():
    reg = SessionRegistry(max_sessions=2)
    reg.reserve_external("k-a-0001")
    reg.reserve_external("k-a-0001")  # same key twice -> counts once
    assert reg.session_count == 1
    reg.release_external("k-a-0001")
    assert reg.session_count == 0
    reg.release_external("k-a-0001")  # releasing an unknown key is a no-op
    assert reg.session_count == 0


@pytest.mark.asyncio
async def test_wait_until_idle_returns_immediately_when_idle():
    reg = SessionRegistry()
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is True


@pytest.mark.asyncio
async def test_wait_until_idle_times_out_when_busy():
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is False


@pytest.mark.asyncio
async def test_wait_until_idle_wakes_on_stop():
    import asyncio
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")

    async def fire_stop():
        await asyncio.sleep(0.02)
        reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")

    asyncio.create_task(fire_stop())
    assert await reg.wait_until_idle("s1", timeout_s=1.0) is True
