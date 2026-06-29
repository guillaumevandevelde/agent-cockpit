import pytest
from app.kanban import session_cleanup


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_cancels_matching_running_run(monkeypatch):
    cancelled = []

    class FakeRun:
        id = 7
        branch = "k-foo-1234"
        status = "running"

    async def fake_find(session_name):
        return FakeRun() if session_name == "k-foo-1234" else None

    async def fake_cancel(run_id):
        cancelled.append(run_id)
        return True

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    monkeypatch.setattr(
        session_cleanup.sandcastle_service, "cancel_run", fake_cancel, raising=False
    )

    ok = await session_cleanup._cancel_sandcastle_run("k-foo-1234")
    assert ok is True
    assert cancelled == [7]


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_noop_when_no_run(monkeypatch):
    async def fake_find(session_name):
        return None

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    assert await session_cleanup._cancel_sandcastle_run("k-none-0000") is False
