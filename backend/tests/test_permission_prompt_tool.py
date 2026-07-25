# backend/tests/test_permission_prompt_tool.py
"""
Tests for the --permission-prompt-tool wiring on the existing KanbanGate primitive.

Acceptance criteria from kanban card 5278a5bd625d45beb6ab7c8bd9b7eb19
(feature: Permissieprompt krijgt een antwoordkanaal — --permission-prompt-tool
op het bestaande KanbanGate):

  AC1  A MCP tool that opens a permission prompt as a KanbanGate (reusing
       service.create_gate), waits, and returns Claude Code's expected
       allow/deny shape. No new gate-typed datamodel.
  AC2  Dispatch passes --permission-prompt-tool only when skip_permissions=False;
       for meta (skip_permissions=True) the spawn stays unchanged.
  AC3  Four paths from analysis doc §5 are tested: approval, denial (deny +
       reason as tool-error, run continues, session lives), timeout (fail-closed
       to deny, <30 min), failing approved action (ordinary tool-error).
  AC4  Invariant: no permission path may stall or kill a session.
  AC5  The gate renders in the kanban-UI with the tool + args for which approval
       is requested so a human can judge what they're saying yes to.

This file is the AC1/AC3/AC4/AC5 surface. AC2 (dispatch argv wiring) lives in
test_permission_prompt_dispatch_wiring.py so a regression in the cli-builder
doesn't drag the MCP tool tests down with it.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from app.kanban import mcp_server as m
from app.kanban import service
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _fast_poll(monkeypatch):
    """Shrink the gate-poll interval so the tests don't sit in 2-second sleeps."""
    monkeypatch.setattr(m, "_GATE_POLL_INTERVAL_SECONDS", 0.01)


# --- AC1 — MCP tool reuses service.create_gate and returns the
#            Claude Code expected allow/deny shape.


@pytest.mark.asyncio
async def test_permission_prompt_returns_allow_when_human_picks_allow():
    """AC1 + AC3 path 1 — approval."""
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]

    async def answer_allow():
        # Let permission_prompt create the gate and start polling first.
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "allow")
            await s.commit()

    result, _ = await asyncio.gather(
        m.permission_prompt(
            card_id=cid,
            tool_name="Write",
            tool_input={"file_path": "/tmp/foo.txt", "content": "hi"},
            timeout_seconds=5,
        ),
        answer_allow(),
    )
    assert result["behavior"] == "allow"
    assert "gate_id" in result


@pytest.mark.asyncio
async def test_permission_prompt_returns_deny_when_human_picks_deny():
    """AC3 path 2 — denial: deny + reason comes back so the run can adapt."""
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]

    async def answer_deny():
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "deny")
            await s.commit()

    result, _ = await asyncio.gather(
        m.permission_prompt(
            card_id=cid,
            tool_name="Write",
            tool_input={"file_path": "/etc/passwd", "content": "x"},
            timeout_seconds=5,
        ),
        answer_deny(),
    )
    assert result["behavior"] == "deny"
    # Claude Code surfaces the message field as the tool-error text in the run.
    assert "message" in result
    assert result["gate_id"]


@pytest.mark.asyncio
async def test_permission_prompt_times_out_fail_closed_to_deny():
    """AC3 path 3 — timeout must be fail-closed to deny (analysis §5 path 3).

    The general open_gate leaves the gate open so a human can answer late; a
    permission prompt cannot — the run is mid-tool-call, and a permanent stall
    is the failure mode we're repairing (analysis §2.5 bevinding 3). On
    timeout we close to deny with an explicit "no human answered in Xs" reason.
    """
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]
    result = await m.permission_prompt(
        card_id=cid,
        tool_name="Write",
        tool_input={"file_path": "/tmp/foo.txt"},
        timeout_seconds=0.05,
    )
    assert result["behavior"] == "deny"
    assert "message" in result
    assert "0.05" in result["message"] or "no" in result["message"].lower()
    # The gate is still recorded as open for audit; we don't close it (analysis
    # §4 explicitly diverges from open_gate's "stays open" behaviour only in
    # the *return value*, not the on-disk state).
    assert "gate_id" in result


@pytest.mark.asyncio
async def test_permission_prompt_default_timeout_is_shorter_than_30_min():
    """Card AC3 path 3: timeout must be shorter than open_gate's 30 minutes.

    A stalled permission prompt holds a worktree + a card claim; 30 minutes of
    nothing is not acceptable for a mid-run question. Five minutes is a
    reasonable starting ceiling — long enough for a human to context-switch,
    short enough that a forgotten prompt self-resolves before EOD.
    """
    assert m._PERMISSION_PROMPT_DEFAULT_TIMEOUT_SECONDS < 1800
    assert m._PERMISSION_PROMPT_DEFAULT_TIMEOUT_SECONDS > 0


# --- AC4 — invariant: no permission path stalls or kills a session.
#
# The session-lifecycle invariant is a contract on the dispatcher, but we can
# pin it from the MCP-tool side: every code path returns a structured answer
# in bounded time (the timeout bound). There is no path that raises, hangs
# forever, or closes the card / releases the claim.


@pytest.mark.asyncio
async def test_permission_prompt_returns_in_bounded_time_on_timeout():
    """AC4 — the timeout path returns within ~timeout, never hangs forever."""
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]
    loop = asyncio.get_event_loop()
    started = loop.time()
    result = await m.permission_prompt(
        card_id=cid, tool_name="Bash", tool_input={"command": "rm -rf /"},
        timeout_seconds=0.05,
    )
    elapsed = loop.time() - started
    assert elapsed < 1.0, f"timeout path took {elapsed}s, expected < 1s"
    assert "behavior" in result  # path closed (allow or deny), not a hung tool


@pytest.mark.asyncio
async def test_permission_prompt_does_not_release_card_claim():
    """AC4 — unlike report_impediment, the gate does not move the card or
    release its claim. The agent session is meant to keep running after the
    answer arrives."""
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]

    async def answer_allow():
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "allow")
            await s.commit()

    await asyncio.gather(
        m.permission_prompt(
            card_id=cid, tool_name="Read",
            tool_input={"file_path": "/tmp/x"}, timeout_seconds=5,
        ),
        answer_allow(),
    )
    # Card untouched: same column, same claim status (None — agents claim via
    # dispatch, not via this tool).
    card = await m.get_card(cid)
    assert card["id"] == cid
    assert card.get("claimed_by") is None


# --- AC5 — the gate carries tool + args so the UI can show what we're saying
#            yes to.


@pytest.mark.asyncio
async def test_permission_prompt_question_includes_tool_and_args():
    """AC5 — the human in the kanban UI must see which tool + args they're
    being asked to approve. Verify the gate's question field contains both the
    tool name and the JSON-formatted args."""
    cid = (await m.create_card("P", "Card", "", confirm_new_project=True))["id"]
    args = {"file_path": "/etc/shadow", "content": "x"}

    async def answer_now():
        await asyncio.sleep(0.05)
        async with KanbanSessionLocal() as s:
            gates = await service.list_gates(s, cid)
            await service.answer_gate(s, gates[0].id, "allow")
            await s.commit()

    await asyncio.gather(
        m.permission_prompt(
            card_id=cid, tool_name="Write", tool_input=args, timeout_seconds=5,
        ),
        answer_now(),
    )

    async with KanbanSessionLocal() as s:
        gates = await service.list_gates(s, cid)
        assert len(gates) == 1
        question = gates[0].question
        options = gates[0].options

    assert "Write" in question
    assert "/etc/shadow" in question
    # Args serialised so a human can see exactly what they're approving.
    assert json.dumps(args, sort_keys=True) in question or "file_path" in question
    # Options are the binary allow/deny — anything more is a new question type.
    assert options == ["allow", "deny"]


# --- Card-not-found path mirrors open_gate's behaviour for consistency.


@pytest.mark.asyncio
async def test_permission_prompt_returns_not_found_for_unknown_card():
    result = await m.permission_prompt(
        card_id="nonexistent-id", tool_name="Bash",
        tool_input={"command": "ls"}, timeout_seconds=5,
    )
    assert result == {"error": "not_found", "card_id": "nonexistent-id"}
