import asyncio

import pytest

from app.services.scheduling.idle_state import IdleState


def test_unknown_session_is_busy_by_default():
    st = IdleState()
    assert st.is_idle("/proj") is False


def test_stop_marks_idle_prompt_marks_busy():
    st = IdleState()
    st.record("SessionStart", cwd="/proj", session_id="s1")
    st.record("Stop", cwd="/proj", session_id="s1")
    assert st.is_idle("/proj") is True
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")
    assert st.is_idle("/proj") is False


@pytest.mark.asyncio
async def test_wait_until_idle_resolves_when_stop_arrives():
    st = IdleState()
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")

    async def fire_stop():
        await asyncio.sleep(0.05)
        st.record("Stop", cwd="/proj", session_id="s1")

    asyncio.create_task(fire_stop())
    became_idle = await st.wait_until_idle("/proj", timeout_s=2)
    assert became_idle is True


@pytest.mark.asyncio
async def test_wait_until_idle_times_out():
    st = IdleState()
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")
    became_idle = await st.wait_until_idle("/proj", timeout_s=0.1)
    assert became_idle is False
