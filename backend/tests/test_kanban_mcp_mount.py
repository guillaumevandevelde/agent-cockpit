"""The kanban MCP server is mounted under /kanban-mcp, so the SSE transport must
advertise its message-POST endpoint at exactly /kanban-mcp/messages/ -- the path
the agent actually POSTs tool calls to.

This must assert on the *runtime-advertised* endpoint event, not on a recomputed
path. The SSE transport builds the advertised URI as ``scope["root_path"] +
endpoint`` at request time; an over-eager ``mount_path`` makes the endpoint itself
already carry the /kanban-mcp prefix, so root_path doubles it to
/kanban-mcp/kanban-mcp/messages/ -- a 404 that silently strands every agent with
zero kanban tools. A test that recomputes ``_normalize_path(...)`` misses this
because it never observes the root_path prefixing that happens on the wire.
"""
import asyncio

from starlette.routing import Match, Mount


async def _advertised_endpoint() -> str:
    """Drive the real app's SSE route over ASGI and return the message endpoint
    it advertises to the client (without the ?session_id=... suffix)."""
    from app.main import app

    path = "/kanban-mcp/sse"
    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
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
    assert captured, "SSE stream never advertised a message endpoint"
    return captured[0].split("?")[0]


def _resolve_post(app, path):
    scope = {"type": "http", "method": "POST", "path": path, "headers": []}
    for route in app.routes:
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return route
    return None


def test_advertised_mcp_message_endpoint_is_the_mount_path():
    advertised = asyncio.run(_advertised_endpoint())
    assert advertised == "/kanban-mcp/messages/", (
        f"SSE advertised {advertised!r}; agents POST tool calls there and must hit "
        "the /kanban-mcp mount, not a doubled/stray path"
    )


def test_advertised_endpoint_routes_to_the_kanban_mount():
    from app.main import app

    advertised = asyncio.run(_advertised_endpoint())
    route = _resolve_post(app, advertised)
    assert route is not None, f"POST {advertised} matches no route (falls through to frontend)"
    assert isinstance(route, Mount) and route.path == "/kanban-mcp", (
        f"POST {advertised} routes to {route!r}, not the kanban-mcp mount"
    )
