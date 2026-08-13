"""Session-aware /messages/ wrapper for the kanban MCP server.

The bare ``SseServerTransport.handle_post_message`` returns ``404 Could not
find session`` as plain text when a POST arrives with an unknown session_id.
The MCP client (sse.py::post_writer) does ``response.raise_for_status()``, and
Claude Code's adapter wraps that non-2xx as ``MCP error -32602: Invalid
request parameters`` — a misleading code, because the params are fine. The
*session* is gone.

When does that happen? The session dict lives in
``SseServerTransport._read_stream_writers`` — pure in-process state. Every
``uvicorn --reload`` respawn (and any other backend restart) wipes it; the
agent's session_id stays the same, but the server no longer recognises it.
The blast radius isn't one session — it's *every dispatched agent mid-card*
that watched its kanban MCP tools silently start returning -32602 (kanban
kaart ``ae19ced1d18646609739cfbb8ff694dd``).

This module replaces the default /messages/ handler with a session-aware
wrapper that returns a **structured JSON 410 Gone** (``error:
session_not_found``) on unknown session_id, and upgrades the pre-existing
plain-text 400s (missing / malformed session_id) to the same JSON shape so
every error path is parseable. The MCP client still sees a non-2xx, but the
exception body now says "MCP session X is no longer valid, the backend was
reloaded, reconnect the SSE stream" — actionable, instead of "Invalid
request parameters" pointing the agent at its own (correct) payload.

Mirrors ``FastMCP.sse_app()`` (mcp/server/fastmcp/server.py:818) but swaps
the /messages/ route's app for the wrapped handler.
"""
from __future__ import annotations

import logging
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)


_SESSION_NOT_FOUND_PAYLOAD: dict = {
    "error": "session_not_found",
    "message": (
        "The MCP session is no longer valid. The backend was reloaded while "
        "the SSE connection was open, so the in-process session registry has "
        "been reset. Please reconnect the SSE stream to get a fresh session."
    ),
    "reconnect_required": True,
}


def make_session_aware_message_handler(sse: SseServerTransport):
    """Return an ASGI handler for /messages/ POSTs that returns a structured
    JSON error on unknown session_id instead of the default plain-text 404.

    On a valid session_id the handler delegates to ``sse.handle_post_message``
    unchanged — the 202-Accepted path every working MCP tool call depends on
    is preserved bit-for-bit.
    """
    original = sse.handle_post_message

    async def handler(scope, receive, send):
        request = Request(scope, receive)
        session_id_param = request.query_params.get("session_id")

        if session_id_param is None:
            response = JSONResponse(
                {"error": "missing_session_id",
                 "message": "session_id query parameter is required"},
                status_code=400,
            )
            await response(scope, receive, send)
            return

        try:
            session_id = UUID(hex=session_id_param)
        except ValueError:
            response = JSONResponse(
                {"error": "invalid_session_id",
                 "message": (
                    f"session_id must be a hex UUID; got {session_id_param!r}"
                 ),
                 "session_id": session_id_param},
                status_code=400,
            )
            await response(scope, receive, send)
            return

        if session_id not in sse._read_stream_writers:
            payload = dict(_SESSION_NOT_FOUND_PAYLOAD)
            payload["session_id"] = session_id_param
            response = JSONResponse(payload, status_code=410)
            logger.info(
                "kanban MCP session_not_found: %s (likely post-reload; "
                "agent should reconnect SSE)", session_id_param,
            )
            await response(scope, receive, send)
            return

        await original(scope, receive, send)

    return handler


def build_session_aware_sse_app(kanban_mcp: FastMCP) -> Starlette:
    """Build an SSE app equivalent to ``kanban_mcp.sse_app()`` but with a
    session-aware /messages/ handler.

    The /sse GET route is unchanged — connect_sse still registers a fresh
    session_id per connection. Only the /messages/ POST route gets the
    wrapped handler.
    """
    settings = kanban_mcp.settings
    sse = SseServerTransport(
        settings.message_path,
        security_settings=settings.transport_security,
    )

    # Match FastMCP.sse_app()'s wiring exactly: the Route endpoint takes a
    # Starlette ``Request`` and delegates to a raw ASGI ``handle_sse`` via
    # ``request._send`` — Starlette Route endpoints receive (Request) and
    # cannot accept the raw ``(scope, receive, send)`` triple directly.
    # Skipping the wrapper makes Starlette pass (Request, ...) positional
    # args and the SSE handshake never starts (regression in
    # test_kanban_mcp_mount.py under the session-aware wrapper).
    async def handle_sse(scope, receive, send):
        async with sse.connect_sse(scope, receive, send) as streams:
            await kanban_mcp._mcp_server.run(
                streams[0], streams[1],
                kanban_mcp._mcp_server.create_initialization_options(),
            )
        return Response()

    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)

    return Starlette(
        routes=[
            Route(settings.sse_path, endpoint=sse_endpoint, methods=["GET"]),
            Mount(settings.message_path,
                  app=make_session_aware_message_handler(sse)),
        ],
    )