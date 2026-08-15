"""Session-aware /messages/ handler (kaart ae19ced1…).

The bare `SseServerTransport.handle_post_message` returns `404 Could not find
session` as plain text when a POST arrives with an unknown session_id. The MCP
client (sse.py::post_writer) does `response.raise_for_status()`, and the Claude
Code adapter wraps any non-2xx from that path as `MCP error -32602: Invalid
request parameters` — the error code is misleading because the params are fine,
the *session* is gone. After a `uvicorn --reload` (or any backend restart) the
in-memory `_read_stream_writers` dict is wiped, every in-flight agent's
session_id becomes unknown, and every subsequent MCP tool call fails with the
same misleading error. The blast radius isn't one session — it's every
dispatched agent mid-card.

The fix: replace the default /messages/ handler with a session-aware wrapper
that returns a structured JSON error (`session_not_found` at 410 Gone) on
unknown session_id, so the agent sees an actionable message ("the backend was
reloaded; reconnect SSE") instead of "Invalid request parameters". Also
upgrades the existing 400 responses (missing/invalid session_id) from plain
text to JSON so every error path is parseable.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.routing import Mount


def _http_scope(method: str, path: str, query: bytes = b"",
                body: bytes = b"") -> dict:
    headers = [
        (b"host", b"localhost:8000"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    return {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "headers": headers, "query_string": query, "scheme": "http",
        "server": ("localhost", 8000), "client": ("127.0.0.1", 1), "root_path": "",
    }


async def _drive_request(app, scope) -> tuple[int, bytes]:
    """Drive a single request through the ASGI app and return (status, body)."""
    # Body is supplied via receive; pick it up from a side channel instead of
    # scanning headers — too easy to misread content-length vs body.
    sent: dict = {}

    async def receive():
        raw_path = scope.get("_test_body", b"")
        return {"type": "http.request", "body": raw_path, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            sent["status"] = msg["status"]
            sent["headers"] = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            sent["body"] = sent.get("body", b"") + msg.get("body", b"")

    await app(scope, receive, send)
    return sent.get("status", 0), sent.get("body", b"")


def _make_dummy_app(handler):
    """Wrap a single ASGI handler in a tiny Starlette app at /messages/."""
    from starlette.applications import Starlette
    return Starlette(routes=[Mount("/messages/", app=handler)])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _bogus_session_handler():
    """Build the session-aware handler under test, pointing at an SSE
    transport whose session dict is empty (so every session_id is bogus).

    Mirrors the production wiring in ``build_session_aware_sse_app`` but
    skips the FastMCP dependency so the test focuses on the handler."""
    from mcp.server.sse import SseServerTransport

    from app.kanban.mcp_transport import make_session_aware_message_handler

    sse = SseServerTransport("/messages/")
    return make_session_aware_message_handler(sse)


def test_bogus_session_id_returns_structured_session_not_found_410():
    """The canonical symptom: POST /messages/?session_id=<bogus> must return
    410 Gone with a JSON body whose `error` field is `session_not_found` —
    not a plain-text 404 that surfaces to the agent as `MCP error -32602:
    Invalid request parameters`."""
    bogus = uuid4().hex
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ping", "arguments": {}}}).encode()
    scope = _http_scope("POST", "/messages/",
                        f"session_id={bogus}".encode(), body)
    scope["_test_body"] = body

    app = _make_dummy_app(_bogus_session_handler())
    status, raw = asyncio.run(_drive_request(app, scope))

    assert status == 410, (
        f"unknown session must return 410 Gone (not the default 404 text); "
        f"got {status} body={raw!r}"
    )
    payload = json.loads(raw)
    assert payload["error"] == "session_not_found"
    assert payload["session_id"] == bogus
    assert payload["reconnect_required"] is True
    # Message must mention the reload cause so the agent can recognise it
    # without guessing — this is the literal text the agent will read.
    msg = payload["message"].lower()
    assert "session" in msg and ("reload" in msg or "reconnect" in msg), (
        f"message must hint at reload+reconnect; got: {payload['message']!r}"
    )


def test_missing_session_id_returns_structured_400_json():
    """The pre-existing 400 path (missing session_id) is upgraded from plain
    text to JSON — keeps the error contract uniform across the handler."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ping", "arguments": {}}}).encode()
    scope = _http_scope("POST", "/messages/", b"", body)
    scope["_test_body"] = body

    app = _make_dummy_app(_bogus_session_handler())
    status, raw = asyncio.run(_drive_request(app, scope))

    assert status == 400
    payload = json.loads(raw)
    assert payload["error"] == "missing_session_id"


def test_invalid_session_id_format_returns_structured_400_json():
    """A non-UUID session_id must return JSON, not plain text."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ping", "arguments": {}}}).encode()
    scope = _http_scope("POST", "/messages/", b"session_id=not-a-uuid", body)
    scope["_test_body"] = body

    app = _make_dummy_app(_bogus_session_handler())
    status, raw = asyncio.run(_drive_request(app, scope))

    assert status == 400
    payload = json.loads(raw)
    assert payload["error"] == "invalid_session_id"
    # The malformed value is echoed back so the agent can self-correct
    # without grepping its own outgoing request.
    assert payload["session_id"] == "not-a-uuid"


def test_known_session_id_delegates_to_original_handler():
    """The happy path: a session_id that IS registered must still be routed
    to the original handle_post_message — the wrapper must not break the
    202-Accepted path that every working MCP tool call depends on."""
    from mcp.server.sse import SseServerTransport

    from app.kanban.mcp_transport import make_session_aware_message_handler

    # Register a session by connecting a fake SSE writer to the dict the way
    # SseServerTransport.connect_sse would. We don't drive a real SSE
    # handshake here — the goal is to prove the wrapper delegates correctly
    # when the session exists. The original handler is what would queue
    # the message; for this test we monkey-patch it to a 202-emitting stub
    # so we can observe delegation without standing up a full SSE session.
    sse = SseServerTransport("/messages/")
    real_session = uuid4()
    sentinel_writer = object()  # any non-None sentinel
    sse._read_stream_writers[real_session] = sentinel_writer

    # Replace handle_post_message on this instance with a stub that returns
    # 202 directly — proves our wrapper calls it when the session exists.
    async def stub(scope, receive, send):
        response = JSONResponse({"accepted": True}, status_code=202)
        await response(scope, receive, send)

    sse.handle_post_message = stub

    handler = make_session_aware_message_handler(sse)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ping", "arguments": {}}}).encode()
    scope = _http_scope("POST", "/messages/",
                        f"session_id={real_session.hex}".encode(), body)
    scope["_test_body"] = body

    app = _make_dummy_app(handler)
    status, raw = asyncio.run(_drive_request(app, scope))

    assert status == 202, (
        f"known session must delegate to the original handler; "
        f"got {status} body={raw!r}"
    )
    assert json.loads(raw) == {"accepted": True}


def test_session_drops_to_not_found_after_disconnect():
    """End-to-end symptom: a session that existed at handshake time becomes
    unknown after the transport drops it from the writers dict (simulates
    the backend-reload path: the old process's writers dict is wiped, a new
    process has an empty one, and the agent's session_id no longer resolves).

    Manually register a session the way ``SseServerTransport.connect_sse``
    does, then drop it (the same finally-block pop the transport itself
    does on SSE disconnect) — and assert the next POST sees a structured
    410 instead of the default 404 text.

    Driving the real ``connect_sse`` context manager from a unit test would
    need a full ASGI app() invocation to drive the EventSourceResponse, which
    is overkill for this assertion — the registration path is well-covered
    by the upstream mcp tests; we only need to prove the wrapper reacts
    correctly once the dict no longer knows the session."""
    from mcp.server.sse import SseServerTransport

    from app.kanban.mcp_transport import make_session_aware_message_handler

    sse = SseServerTransport("/messages/")
    handler = make_session_aware_message_handler(sse)
    app = _make_dummy_app(handler)

    # Register a session the same way connect_sse does, then drop it.
    sid = uuid4()
    sentinel_writer = object()
    sse._read_stream_writers[sid] = sentinel_writer
    assert sid in sse._read_stream_writers, (
        "sanity: registration must work — wrapper test is meaningless otherwise"
    )
    # Simulate the SSE finally-block pop (or a backend reload — same effect).
    sse._read_stream_writers.pop(sid)
    assert sid not in sse._read_stream_writers

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ping", "arguments": {}}}).encode()
    scope = _http_scope("POST", "/messages/",
                        f"session_id={sid.hex}".encode(), body)
    scope["_test_body"] = body
    status, raw = asyncio.run(_drive_request(app, scope))

    assert status == 410, (
        f"after the SSE session drops (simulating a backend reload), the "
        f"very next POST must surface a structured 410 Gone; got {status} "
        f"body={raw!r}"
    )
    payload = json.loads(raw)
    assert payload["error"] == "session_not_found"
    assert payload["session_id"] == sid.hex