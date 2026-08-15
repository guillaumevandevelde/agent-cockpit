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


@pytest.mark.asyncio
async def test_list_team_includes_registered_member(tmp_path):
    await _call("agent_mail_whoami", {"cwd": str(tmp_path / "a"), "session_key": "cc:a"})
    body = await _call("agent_mail_list_team", {"cwd": str(tmp_path / "a"), "session_key": "cc:a"})
    names = {m["display_name"] for m in body}
    assert (tmp_path / "a").name in names
