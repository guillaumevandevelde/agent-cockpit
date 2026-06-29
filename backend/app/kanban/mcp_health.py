"""End-to-end self-check for the kanban MCP wiring.

The MCP failure mode that strands agents is silent: the SSE GET stays open (200)
while the advertised message endpoint 404s, so the agent gets zero tools and no
error surfaces anywhere. ``check_mcp_health`` makes that failure *loud* by
reproducing what an agent's first round-trip depends on, in-process:

  1. read the message endpoint the SSE transport actually advertises on the wire
     (this is where the doubled-mount_path bug shows up -- root_path gets prefixed
     a second time, yielding /kanban-mcp/kanban-mcp/messages/);
  2. confirm that advertised path routes to the kanban MCP mount, not the
     StaticFiles frontend / a 404 (the agent POSTs every tool call there);
  3. confirm the server actually registers tools;
  4. confirm the kanban store answers.

Surface the result in the UI so a broken mount (a doubled prefix, a dependency
regression, a port change) is visible at a glance instead of only manifesting as
agents that mysteriously never touch their cards.

Driven directly over ASGI (raw scope), not via an HTTP client: an in-process
duplex SSE handshake over a real socket deadlocks, but reading just the first
advertised-endpoint event off the ASGI stream is reliable and fast.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MOUNT_PREFIX = "/kanban-mcp"


async def check_mcp_health(*, app=None, mcp=None, mount_prefix: str = _MOUNT_PREFIX) -> dict:
    """Probe the mounted kanban MCP server. Never raises; returns a dict:

        ok: bool                      -- every check passed
        advertised_endpoint: str|None -- message path the SSE stream advertises
        routes_to_mount: bool         -- that path resolves to the MCP mount
        tools: list[str]              -- registered tool names
        db_ok: bool                   -- the kanban store answered SELECT 1
        error: str|None               -- first failure reason, for the UI
    """
    if app is None:
        from app.main import app as _app
        app = _app
    if mcp is None:
        from app.kanban.mcp_server import mcp as _mcp
        mcp = _mcp

    result: dict = {
        "ok": False, "advertised_endpoint": None, "routes_to_mount": False,
        "message_post_status": None, "tools": [], "db_ok": False, "error": None,
    }

    # 1 + 2: reproduce an agent's first round-trip end to end -- open the SSE
    # stream, read the message endpoint it advertises, and POST a real ping back
    # to it *with the session_id from the handshake, while that session is still
    # live*. A correctly wired mount answers 202 Accepted (the message was queued
    # for the server). That is the unambiguous healthy signal: a doubled mount
    # makes the agent POST to a path that 404s, and a POST that falls through to
    # the StaticFiles frontend 405s -- only 202 means a tool call would actually
    # land on the MCP server. (Posting *without* a session_id, as an earlier probe
    # did, always drew a 400 "session_id is required"; that proved routing but read
    # as a failure and logged a warning on every check.)
    try:
        advertised, status_code = await asyncio.wait_for(
            _message_roundtrip(app, f"{mount_prefix}/sse"), timeout=8.0
        )
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - probe must not raise
        advertised, status_code = None, 0
        result["error"] = f"could not probe MCP message endpoint: {e}"
    if advertised:
        result["advertised_endpoint"] = advertised
        result["message_post_status"] = status_code
        result["routes_to_mount"] = status_code == 202
        if not result["routes_to_mount"] and result["error"] is None:
            result["error"] = (
                f"advertised message endpoint {advertised} returned {status_code} "
                "(expected 202 Accepted) -- agent tool calls would not reach the MCP "
                "mount (doubled prefix, a 404, or the frontend intercepting the POST)"
            )

    # 3: tools registered.
    try:
        result["tools"] = sorted(t.name for t in await mcp.list_tools())
    except Exception as e:  # noqa: BLE001
        result["error"] = result["error"] or f"could not list tools: {e}"
    if not result["tools"] and result["error"] is None:
        result["error"] = "MCP server registers no tools"

    # 4: kanban store answers.
    try:
        from sqlalchemy import text
        from app.kanban.db import KanbanSessionLocal
        async with KanbanSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        result["db_ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = result["error"] or f"kanban store unreachable: {e}"

    result["ok"] = bool(
        result["routes_to_mount"] and result["tools"] and result["db_ok"]
    )
    return result


async def _message_roundtrip(app, sse_path: str) -> tuple[Optional[str], int]:
    """Open the SSE stream, read the advertised message endpoint, and POST a ping
    back to it using the session_id from the handshake -- all while the SSE task
    (and therefore the session) is still alive. Returns
    ``(advertised_path_without_session, post_status)``; ``(None, 0)`` if the SSE
    stream never advertises an endpoint.

    The SSE task is left running until the POST completes so the session writer is
    still registered when the message lands; a sessionless POST would only ever
    draw a 400."""
    scope = {
        "type": "http", "method": "GET", "path": sse_path, "raw_path": sse_path.encode(),
        "headers": [(b"host", b"localhost:8000")], "query_string": b"",
        "scheme": "http", "server": ("localhost", 8000), "client": ("127.0.0.1", 1),
        "root_path": "", "app": app,
    }
    captured: list[str] = []
    done = asyncio.Event()

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.body":
            for line in msg.get("body", b"").decode(errors="replace").splitlines():
                if line.startswith("data:") and "messages" in line:
                    captured.append(line[len("data:"):].strip())
                    done.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(done.wait(), timeout=5)
        if not captured:
            return None, 0
        advertised = captured[0]
        status = await asyncio.wait_for(_post_status(app, advertised), timeout=5)
        return advertised.split("?")[0], status
    finally:
        task.cancel()


async def _post_status(app, path: str) -> int:
    """Drive a real POST through the ASGI app to ``path`` (keeping its
    ?session_id=... query string) and return the response status. 202 means the
    message route exists, found the live session, and queued the message; 404
    means no handler is wired there (the doubled-path symptom)."""
    bare, _, query = path.partition("?")
    body = b'{"jsonrpc":"2.0","id":0,"method":"ping"}'
    scope = {
        "type": "http", "method": "POST", "path": bare, "raw_path": bare.encode(),
        "headers": [(b"host", b"localhost:8000"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
        "query_string": query.encode(), "scheme": "http", "server": ("localhost", 8000),
        "client": ("127.0.0.1", 1), "root_path": "", "app": app,
    }
    captured: dict = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]

    await app(scope, receive, send)
    return captured.get("status", 0)
