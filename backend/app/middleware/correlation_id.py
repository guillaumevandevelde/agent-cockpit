"""Middleware that attaches a correlation ID to every request.

Implemented as a plain ASGI middleware (not `starlette.middleware.base.
BaseHTTPMiddleware`) on purpose: Starlette's `BaseHTTPMiddleware` runs
`call_next` in a separate task per request, and nesting two or more
`BaseHTTPMiddleware`-based middlewares under concurrent load can cross-wire
the response streams of different in-flight requests, raising
`AssertionError: Unexpected message: {'type': 'http.response.start', ...}`
and corrupting one of them. This app also runs `require_api_token`
(app/main.py) as a plain ASGI middleware for the same reason.
"""
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import set_correlation_id

_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cid = Headers(scope=scope).get(_HEADER) or str(uuid.uuid4())
        set_correlation_id(cid)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(_HEADER, cid)
            await send(message)

        await self.app(scope, receive, send_wrapper)
