"""End-to-end self-check for the kanban MCP wiring.

check_mcp_health() must report ok=True with the real tool list when the mount is
sound (routing works AND a real tools/call succeeds), and ok=False (not raise,
not hang) both when the advertised message endpoint is mis-wired -- the
doubled-mount_path class of bug -- and when routing is fine but tool calls are
rejected over the SSE response (the -32602 stale/uninitialized-session class of
bug). This is the signal the UI surfaces.
"""
import asyncio

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from app.kanban.mcp_health import check_mcp_health


def test_mcp_health_is_ok_for_the_real_app():
    result = asyncio.run(check_mcp_health())
    assert result["ok"] is True, result
    assert result["advertised_endpoint"] == "/kanban-mcp/messages/", result
    assert result["routes_to_mount"] is True, result
    # A genuine agent round-trip (SSE handshake + POST with its session_id) is
    # accepted; the probe must observe 202, not the 400 a sessionless POST drew.
    assert result["message_post_status"] == 202, result
    # And the deeper signal: a real tools/call to `ping` came back without error,
    # so a stale/uninitialized session (-32602 on every request) would flip ok.
    assert result["tool_call_ok"] is True, result
    assert result["protocol_version"], result
    assert "list_cards" in result["tools"], result
    assert result["db_ok"] is True, result


def _doubled_app():
    """A deliberately mis-wired mount: mount_path doubles the prefix, exactly the
    regression that broke agent tool calls."""
    ts = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp = FastMCP("probe", transport_security=ts)
    app = Starlette()
    app.mount("/kanban-mcp", mcp.sse_app(mount_path="/kanban-mcp"))
    return app, mcp


def test_mcp_health_flags_a_doubled_mount_path():
    app, mcp = _doubled_app()
    result = asyncio.run(check_mcp_health(app=app, mcp=mcp))
    assert result["ok"] is False
    assert result["advertised_endpoint"] == "/kanban-mcp/kanban-mcp/messages/", result
    assert result["routes_to_mount"] is False
    assert result["tool_call_ok"] is False
    assert result["error"]


def test_mcp_health_flags_a_rejected_tool_call():
    """Routing is sound (the POST reaches the mount, 202) but the MCP session
    rejects the tools/call over the SSE response -- the -32602 stale/uninitialized
    session symptom. The probe must catch it via tool_call_ok=False rather than
    reporting healthy off the 202 alone.

    We simulate a session that answers every routed request with a JSON-RPC error
    by mounting a fake ASGI app whose SSE stream advertises a working message
    endpoint but whose message handler always replies with -32602.
    """
    app = _rejecting_mcp_app()

    class _NoTools:
        async def list_tools(self):
            return []

    result = asyncio.run(check_mcp_health(app=app, mcp=_NoTools(), mount_prefix="/kanban-mcp"))
    assert result["routes_to_mount"] is True, result
    assert result["tool_call_ok"] is False, result
    assert result["ok"] is False, result
    assert result["error"], result


def _rejecting_mcp_app():
    """Minimal ASGI app that mimics a routed-but-rejecting MCP mount: the SSE GET
    advertises a message endpoint, and every POST there is queued (202) but pushed
    back over the SSE stream as a -32602 JSON-RPC error (matching the observed
    stale-session failure)."""
    import json as _json

    pending: asyncio.Queue = asyncio.Queue()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope["path"]
        if scope["method"] == "GET" and path.endswith("/sse"):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            endpoint = "event: endpoint\r\ndata: /kanban-mcp/messages/?session_id=x\r\n\r\n"
            await send({"type": "http.response.body", "body": endpoint.encode(),
                        "more_body": True})
            while True:
                req_id = await pending.get()
                frame = (f"event: message\r\ndata: "
                         f'{_json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Invalid request parameters", "data": ""}})}'
                         "\r\n\r\n")
                await send({"type": "http.response.body", "body": frame.encode(),
                            "more_body": True})
        elif scope["method"] == "POST" and "/messages/" in path:
            msg = await receive()
            try:
                obj = _json.loads(msg.get("body", b"{}"))
            except _json.JSONDecodeError:
                obj = {}
            if obj.get("id") is not None:
                await pending.put(obj["id"])
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b"Accepted"})
        else:
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    return app
