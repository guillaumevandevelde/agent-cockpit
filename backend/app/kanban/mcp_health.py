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

    # 1 + 2: the advertised message endpoint and whether it routes to the mount.
    try:
        advertised = await asyncio.wait_for(
            _advertised_endpoint(app, f"{mount_prefix}/sse"), timeout=5.0
        )
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - probe must not raise
        advertised = None
        result["error"] = f"could not read advertised endpoint: {e}"
    if advertised:
        result["advertised_endpoint"] = advertised
        # A top-level Mount match is too coarse: the doubled path still matches the
        # outer /kanban-mcp mount by prefix, then 404s *inside* the sub-app. Drive a
        # real POST through ASGI and look at the status -- 404 means the agent's tool
        # calls land on no handler (the actual symptom).
        try:
            status_code = await asyncio.wait_for(_post_status(app, advertised), timeout=5.0)
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            status_code = 0
            result["error"] = result["error"] or f"could not probe message endpoint: {e}"
        result["message_post_status"] = status_code
        result["routes_to_mount"] = status_code not in (0, 404)
        if not result["routes_to_mount"] and result["error"] is None:
            result["error"] = (
                f"advertised message endpoint {advertised} returns {status_code} -- agent "
                "tool calls land on a 404 / the frontend instead of the MCP mount"
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


async def _advertised_endpoint(app, sse_path: str) -> Optional[str]:
    """Drive the SSE route over raw ASGI and return the advertised message path
    (without the ?session_id=... suffix), or None if none is emitted."""
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
    finally:
        task.cancel()
    return captured[0].split("?")[0] if captured else None


async def _post_status(app, path: str) -> int:
    """Drive a real POST through the ASGI app to ``path`` and return the response
    status. 404 means no handler is wired there (the doubled-path symptom); any
    other status means the message route exists and answered."""
    bare = path.split("?")[0]
    body = b'{"jsonrpc":"2.0","id":0,"method":"ping"}'
    scope = {
        "type": "http", "method": "POST", "path": bare, "raw_path": bare.encode(),
        "headers": [(b"host", b"localhost:8000"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
        "query_string": b"", "scheme": "http", "server": ("localhost", 8000),
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
