"""Session-cleanup triggers from kanban ops.

Regression for kanban card 28b578ba ("Dispatch-sessie termineert niet na
move_card→Done/Impediment (MCP-pad)"). The promised contract — "the
backend will kill this session and remove the worktree" (see
backend/app/kanban/dispatch.py:905) — must hold for *every* transition
that an agent ends the card on: Done *and* Impediment, via MCP *and* REST.
A `release` op alone (no column change) is a separate decision and must
NOT trigger cleanup; the fix scope is column-transitions to terminal
columns only.

Patches `app.kanban.session_cleanup.cleanup_session_for_card` to a
recording AsyncMock and yields to the event loop until the scheduled
task runs (mirrors the pattern in
`tests/test_session_cleanup.py::test_scheduled_task_actually_runs_after_gc`).
"""
import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from app.kanban import session_cleanup
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _wait_for_cleanup(called: list, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not called and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)


def _patch_cleanup(monkeypatch, called: list) -> AsyncMock:
    async def fake_cleanup(card_id, project_key, claimed_by=None):
        called.append((card_id, project_key))
        return {"cleaned": True}

    monkeypatch.setattr(session_cleanup, "cleanup_session_for_card", fake_cleanup)
    return fake_cleanup


# --- Done via MCP move_card: this path already worked (regression guard). -


@pytest.mark.asyncio
async def test_move_to_done_via_mcp_fires_cleanup(monkeypatch):
    called: list = []
    _patch_cleanup(monkeypatch, called)

    card = await m.create_card("git:test/repo", "X", "", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:k-test-1234")

    await m.move_card(cid, "Done", summary="Built and shipped.")
    await _wait_for_cleanup(called)

    assert called == [(cid, "git:test/repo")], called


# --- Impediment via MCP move_card: must also fire cleanup. ----------------


@pytest.mark.asyncio
async def test_move_to_impediment_via_mcp_fires_cleanup(monkeypatch):
    called: list = []
    _patch_cleanup(monkeypatch, called)

    card = await m.create_card("git:test/repo", "X", "", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:k-test-1234")

    await m.move_card(cid, "Impediment", summary="Stuck on auth setup.")
    await _wait_for_cleanup(called)

    assert called == [(cid, "git:test/repo")], called


# --- report_impediment (MCP): the bug from card 28b578ba. ----------------


@pytest.mark.asyncio
async def test_report_impediment_via_mcp_fires_cleanup(monkeypatch):
    called: list = []
    _patch_cleanup(monkeypatch, called)

    card = await m.create_card("git:test/repo", "X", "", confirm_new_project=True)
    cid = card["id"]
    await m.claim_card(cid, "agent:k-test-1234")

    await m.report_impediment(
        cid, "Which API key should I use?",
        options=["Project key", "Personal key", "Ask ops", "Skip the call"],
    )
    await _wait_for_cleanup(called)

    assert called == [(cid, "git:test/repo")], called


# --- Same path via REST apply_operation: the trigger must live on the --- #
# --- materializer, not the MCP wrapper, so a non-MCP caller gets the --- #
# --- same contract. ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_move_to_impediment_via_rest_fires_cleanup(monkeypatch):
    called: list = []
    _patch_cleanup(monkeypatch, called)

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:test/repo", entity_id=None,
            payload={"title": "X", "description": "", "column": "Doing"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": "agent:k-test-1234"},
        )
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="", entity_id=cid,
            payload={"column": "Impediment"},
        )
        await s.commit()

    await _wait_for_cleanup(called)

    assert called == [(cid, "git:test/repo")], called


# --- Negative: a bare `release` (no column change) must NOT fire cleanup. -- #
# --- Fix scope is Done ∪ Impediment, not "every release on an agent" --- #
# --- column. Including this case would silently kill in-flight work on --- #
# --- every user-typed `release_card` from the UI. ----------------------- #


@pytest.mark.asyncio
async def test_release_without_move_does_not_fire_cleanup(monkeypatch):
    called: list = []
    _patch_cleanup(monkeypatch, called)

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:test/repo", entity_id=None,
            payload={"title": "X", "description": "", "column": "Doing"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": "agent:k-test-1234"},
        )
        await apply_operation(
            s, op_type="release", entity_type="card",
            project_key="", entity_id=cid, payload={},
        )
        await s.commit()

    # Wait long enough that a wrongly-scheduled task would have run.
    await _wait_for_cleanup(called, timeout=0.3)

    assert called == [], called
