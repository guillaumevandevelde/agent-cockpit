"""Middleware that attaches a correlation ID to every request."""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import set_correlation_id

_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        cid = request.headers.get(_HEADER) or str(uuid.uuid4())
        set_correlation_id(cid)
        response = await call_next(request)
        response.headers[_HEADER] = cid
        return response
