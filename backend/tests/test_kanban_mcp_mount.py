"""Bug #2: the kanban MCP server is mounted under /kanban-mcp, so the SSE
transport must advertise its message-POST endpoint *with that prefix*. Without
mount_path, FastMCP advertises a bare /messages/, which Starlette routes to the
StaticFiles frontend instead of the MCP handler -- every tool call (the writes
the agent makes) silently misses the server. This is the -32602 class.
"""
from starlette.routing import Match, Mount


def _resolve_post(app, path):
    scope = {"type": "http", "method": "POST", "path": path, "headers": []}
    for route in app.routes:
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return route
    return None


def test_advertised_mcp_message_endpoint_reaches_the_mcp_mount():
    # Importing the app wires the mount; the advertised endpoint is derived from
    # the same FastMCP settings the running server uses.
    from app.main import app
    from app.kanban.mcp_server import mcp

    advertised = mcp._normalize_path(mcp.settings.mount_path, mcp.settings.message_path)
    assert advertised.startswith("/kanban-mcp/")

    route = _resolve_post(app, advertised)
    assert route is not None, f"POST {advertised} matches no route (falls through)"
    assert isinstance(route, Mount) and route.path == "/kanban-mcp", (
        f"POST {advertised} routes to {route!r}, not the kanban-mcp mount"
    )
