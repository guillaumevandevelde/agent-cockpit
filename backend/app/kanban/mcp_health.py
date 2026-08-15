"""End-to-end self-check for the kanban MCP wiring.

Two distinct failure modes strand agents silently, and both must be made *loud*:

  A. **Routing** — the SSE GET stays open (200) while the advertised message
     endpoint 404s (the doubled-mount_path bug: root_path gets prefixed twice,
     yielding /kanban-mcp/kanban-mcp/messages/). The agent gets zero tools and no
     error surfaces anywhere.
  B. **Protocol** — routing is fine (the POST is queued, 202) but the tool call
     itself is *rejected* over the SSE response with a JSON-RPC error. The one
     observed in the wild: a session whose server-side state was never
     initialized (backend restarted/reconnected after the agent's handshake)
     answers **every** request — including `ping` — with
     ``-32602 Invalid request parameters``. A POST returning 202 says nothing
     about this; only reading the actual JSON-RPC *response* does.

``check_mcp_health`` reproduces what an agent's tool call actually depends on,
in-process and end to end: open the SSE stream, complete the ``initialize``
handshake, then issue a real ``tools/call`` to the ``ping`` tool and read its
JSON-RPC result back off the SSE stream. Healthy means that result comes back
without error — not merely that the POST was accepted. This closes the gap where
the earlier probe (which only checked for a 202 on a bare, session-less-but-
routed ping) reported ``ok: true`` while every genuine tool call got -32602.

Surface the result in the UI so a broken mount, a protocol regression, or a
stale/uninitialized session is visible at a glance instead of only manifesting
as agents that mysteriously never touch their cards.

Driven directly over ASGI (raw scope), not via an HTTP client: an in-process
duplex SSE handshake over a real socket deadlocks, but driving the ASGI app with
our own receive/send and reading response frames off the SSE stream is reliable.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_MOUNT_PREFIX = "/kanban-mcp"
_PROTOCOL_VERSION = "2025-06-18"


async def check_mcp_health(*, app, mcp=None, mount_prefix: str = _MOUNT_PREFIX) -> dict:
    """Probe the mounted kanban MCP server. Never raises; returns a dict:

        ok: bool                      -- every check passed
        advertised_endpoint: str|None -- message path the SSE stream advertises
        routes_to_mount: bool         -- the tools/call POST reached the mount (202)
        message_post_status: int|None -- HTTP status of the tools/call POST
        tool_call_ok: bool            -- tools/call ping returned a non-error result
        protocol_version: str|None    -- version negotiated by the initialize handshake
        tools: list[str]              -- registered tool names
        db_ok: bool                   -- the kanban store answered SELECT 1
        error: str|None               -- first failure reason, for the UI
    """
    # ``app`` is verplicht: dit is de domeinlaag, en een fallback naar
    # ``app.main`` trok de hele transportlaag hier naar binnen (de enige
    # overtreding van het "transport is een blad"-contract). De route geeft
    # ``request.app`` mee; een test geeft de app die hij wil meten.
    if mcp is None:
        from app.kanban.mcp_server import mcp as _mcp
        mcp = _mcp

    result: dict = {
        "ok": False, "advertised_endpoint": None, "routes_to_mount": False,
        "message_post_status": None, "tool_call_ok": False,
        "protocol_version": None, "tools": [], "db_ok": False, "error": None,
    }

    # 1: reproduce an agent's tool call end to end -- open the SSE stream,
    # complete the initialize handshake, then POST a real tools/call for the
    # `ping` tool and read its JSON-RPC result back off the SSE stream. Only a
    # non-error result means a genuine tool call would land and succeed: a
    # doubled mount makes the POST 404 (routes_to_mount stays False), and an
    # uninitialized/stale session answers 202-then-`-32602` over SSE
    # (routes_to_mount True but tool_call_ok False).
    try:
        probe = await asyncio.wait_for(
            _tool_call_roundtrip(app, f"{mount_prefix}/sse"), timeout=12.0
        )
    except (TimeoutError, Exception) as e:  # noqa: BLE001 - probe must not raise
        probe = {"advertised": None, "post_status": 0, "protocol_version": None,
                 "tool_call_ok": False, "error": f"could not probe MCP tool call: {e}"}

    result["advertised_endpoint"] = probe["advertised"]
    result["message_post_status"] = probe["post_status"]
    result["protocol_version"] = probe["protocol_version"]
    result["routes_to_mount"] = probe["post_status"] == 202
    result["tool_call_ok"] = probe["tool_call_ok"]
    if probe["error"]:
        result["error"] = probe["error"]
    elif not result["routes_to_mount"]:
        result["error"] = (
            f"advertised message endpoint {probe['advertised']} returned "
            f"{probe['post_status']} (expected 202 Accepted) -- agent tool calls "
            "would not reach the MCP mount (doubled prefix, a 404, or the frontend "
            "intercepting the POST)"
        )
    elif not result["tool_call_ok"]:
        result["error"] = (
            "tools/call ping was routed (202) but rejected over the SSE response -- "
            "the MCP session answers requests with a JSON-RPC error (e.g. -32602 on a "
            "stale/uninitialized session after a backend restart); agents would fail "
            "every tool call"
        )

    # 2: tools registered.
    try:
        result["tools"] = sorted(t.name for t in await mcp.list_tools())
    except Exception as e:  # noqa: BLE001
        result["error"] = result["error"] or f"could not list tools: {e}"
    if not result["tools"] and result["error"] is None:
        result["error"] = "MCP server registers no tools"

    # 3: kanban store answers.
    try:
        from sqlalchemy import text

        from app.kanban.db import KanbanSessionLocal
        async with KanbanSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        result["db_ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = result["error"] or f"kanban store unreachable: {e}"

    result["ok"] = bool(
        result["routes_to_mount"] and result["tool_call_ok"]
        and result["tools"] and result["db_ok"]
    )
    return result


async def _tool_call_roundtrip(app, sse_path: str) -> dict:
    """Open the SSE stream and drive a full MCP handshake + a real ``tools/call``
    to the ``ping`` tool, reading each JSON-RPC response back off the SSE stream --
    the exact round-trip an agent's tool call depends on. Returns:

        advertised: str|None      -- message endpoint (session query stripped)
        post_status: int          -- HTTP status of the tools/call POST (202 healthy)
        protocol_version: str|None
        tool_call_ok: bool        -- tools/call ping returned a non-error result
        error: str|None

    Never raises. The SSE task is left running until the whole exchange completes
    so the session writer stays registered for every response frame."""
    out: dict = {"advertised": None, "post_status": 0, "protocol_version": None,
                 "tool_call_ok": False, "error": None}

    scope = _http_scope("GET", sse_path)
    frames: asyncio.Queue = asyncio.Queue()
    current_event: list = [None]

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(msg):
        # Parse the SSE byte stream into (event, data) frames as they arrive.
        if msg.get("type") != "http.response.body":
            return
        for line in msg.get("body", b"").decode(errors="replace").splitlines():
            if line.startswith("event:"):
                current_event[0] = line[len("event:"):].strip()
            elif line.startswith("data:"):
                await frames.put((current_event[0], line[len("data:"):].strip()))
            elif line == "":
                current_event[0] = None

    task = asyncio.create_task(app(scope, receive, send))
    try:
        # First frame advertises the message endpoint (with ?session_id=...).
        ev, data = await asyncio.wait_for(frames.get(), timeout=5)
        if ev != "endpoint" or "messages" not in data:
            return out
        advertised_full = data
        out["advertised"] = advertised_full.split("?")[0]

        async def await_response(want_id: int, timeout: float = 5.0) -> dict | None:
            while True:
                fev, fdata = await asyncio.wait_for(frames.get(), timeout=timeout)
                if fev != "message":
                    continue
                try:
                    obj = json.loads(fdata)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == want_id:
                    return obj

        # initialize handshake.
        init_status = await _post(app, advertised_full, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "cockpit-mcp-health", "version": "1"}},
        })
        if init_status != 202:
            out["post_status"] = init_status
            return out
        init_resp = await await_response(1)
        if init_resp and isinstance(init_resp.get("result"), dict):
            out["protocol_version"] = init_resp["result"].get("protocolVersion")
        await _post(app, advertised_full,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # The real tool call an agent makes.
        call_status = await _post(app, advertised_full, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "ping", "arguments": {}},
        })
        out["post_status"] = call_status
        if call_status != 202:
            return out
        call_resp = await await_response(2)
        if call_resp is not None:
            out["tool_call_ok"] = (
                call_resp.get("error") is None
                and not (isinstance(call_resp.get("result"), dict)
                         and call_resp["result"].get("isError"))
            )
    except (TimeoutError, Exception) as e:  # noqa: BLE001 - probe must not raise
        out["error"] = f"MCP tool-call round-trip failed: {e}"
    finally:
        task.cancel()
    return out


def _http_scope(method: str, path: str, query: bytes = b"", extra_headers=None) -> dict:
    headers = [(b"host", b"localhost:8000")]
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "headers": headers, "query_string": query, "scheme": "http",
        "server": ("localhost", 8000), "client": ("127.0.0.1", 1), "root_path": "",
    }


async def _post(app, advertised_path: str, payload: dict) -> int:
    """Drive a real POST of ``payload`` through the ASGI app to the advertised
    message endpoint (keeping its ?session_id=... query string) and return the
    response status. 202 means the message route exists, found the live session,
    and queued the message; 404 means no handler is wired there (doubled path)."""
    bare, _, query = advertised_path.partition("?")
    body = json.dumps(payload).encode()
    scope = _http_scope("POST", bare, query.encode(), extra_headers=[
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ])
    captured: dict = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]

    await app(scope, receive, send)
    return captured.get("status", 0)
