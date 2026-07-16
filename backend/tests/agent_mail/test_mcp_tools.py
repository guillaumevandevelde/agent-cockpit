import json

import pytest

from app.mcp_server.server import mcp
# Schema + per-test reset handled by ``_reset_app_database_tables`` in
# conftest.py, and ``_patch_app_database`` identity-swaps
# ``AsyncSessionLocal`` on every module that did
# ``from app.database import AsyncSessionLocal`` at import time — including
# ``app.mcp_server.tools.agent_mail`` — so the MCP tool layer that builds
# its own sessions now opens them on the test engine without any per-file
# monkeypatch.


async def _call(tool_name: str, args: dict) -> dict:
    result = await mcp.call_tool(tool_name, args)
    # FastMCP's call_tool returns (content_blocks, {"result": <raw return value>})
    raw = result[1]["result"]
    return json.loads(raw)


@pytest.mark.asyncio
async def test_whoami_registers_and_returns_member(tmp_path):
    body = await _call("agent_mail_whoami", {"cwd": str(tmp_path), "session_key": "cc:test-1"})
    assert body["member"]["repo_name"] == tmp_path.name
    assert body["unread_count"] == 0


@pytest.mark.asyncio
async def test_send_message_then_check_inbox(tmp_path):
    body1 = await _call("agent_mail_whoami", {"cwd": str(tmp_path / "a"), "session_key": "cc:a"})
    body2 = await _call("agent_mail_whoami", {"cwd": str(tmp_path / "b"), "session_key": "cc:b"})
    member_b = body2["member"]["id"]

    send_body = await _call("agent_mail_send_message", {
        "cwd": str(tmp_path / "a"), "session_key": "cc:a",
        "to_member_id": member_b, "body": "hi from a",
    })
    assert send_body["ok"] is True
    assert body1["member"]["id"] != member_b

    # check_inbox marks fetched messages read as part of the same call (matching
    # upstream's deck_check_inbox), so the message shows up here with read_at set
    # and unread_count already reflects the post-mark-read state (0), not the
    # pre-call state — that's the intended "check inbox = consume it" behavior.
    inbox_body = await _call("agent_mail_check_inbox", {
        "cwd": str(tmp_path / "b"), "session_key": "cc:b", "unread_only": True,
    })
    assert len(inbox_body["messages"]) == 1
    assert inbox_body["messages"][0]["body_markdown"] == "hi from a"
    assert inbox_body["messages"][0]["read_at"] is not None
    assert inbox_body["unread_count"] == 0


@pytest.mark.asyncio
async def test_request_context_and_reply_as_answer(tmp_path):
    await _call("agent_mail_whoami", {"cwd": str(tmp_path / "a"), "session_key": "cc:a2"})
    body2 = await _call("agent_mail_whoami", {"cwd": str(tmp_path / "b"), "session_key": "cc:b2"})
    member_b = body2["member"]["id"]

    req = await _call("agent_mail_request_context", {
        "cwd": str(tmp_path / "a"), "session_key": "cc:a2",
        "to_member_id": member_b, "topic": "how does X work", "why_needed": "need to understand",
    })
    assert req["ok"] is True

    reply = await _call("agent_mail_reply", {
        "cwd": str(tmp_path / "b"), "session_key": "cc:b2",
        "thread_root_id": req["message_id"], "body": "here is context",
    })
    assert reply["ok"] is True
    assert reply["kind"] == "answer"
