"""Regression test for the /api/v1/mcp-server tools/call JSON-RPC handler.

FastMCP's call_tool(..., convert_result=True) returns a (content_blocks,
structured_result) tuple for any tool with an output schema (every @mcp.tool()
in this codebase, since they all return a plain str). The handler used to
iterate that tuple directly as if it were the content list, producing two
"items" -- the list and the dict -- neither of which has `.text`, so the
str(item) fallback returned a mangled repr instead of the tool's real output.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _create_token(client: AsyncClient) -> str:
    r = await client.post("/api/v1/mcp-server/tokens", json={"name": "tools-call-test", "scope": "write"})
    assert r.status_code == 200
    return r.json()["token"]


@pytest.mark.asyncio
async def test_tools_call_returns_clean_tool_output_not_tuple_repr():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _create_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.post("/api/v1/mcp-server", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_scheduled_messages", "arguments": {}},
        })
        assert r.status_code == 200
        body = r.json()

        content = body["result"]["content"]
        assert len(content) == 1, "tools/call must return exactly one content block, not the raw tuple's two elements"
        text = content[0]["text"]
        assert "TextContent(" not in text, "content block leaked FastMCP's internal repr instead of the tool's output"
        assert "{'result':" not in text, "content block leaked the structured-result dict repr"
        parsed = json.loads(text)
        assert "items" in parsed and "total" in parsed
