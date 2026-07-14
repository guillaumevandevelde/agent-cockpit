"""Structured JSON logging configuration with per-request correlation IDs."""
import logging
import sys
import time
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


class _CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


class _UtcJsonFormatter(JsonFormatter):
    """JsonFormatter that emits ``timestamp`` in UTC ISO 8601 with a ``Z`` suffix.

    The default Python ``logging.Formatter`` writes ``%(asctime)s`` in local
    time; on this host that is CEST (UTC+2). Kanban card timestamps
    (``created_at``, activity feed, ``impediment_question`` channel) are
    naive datetimes coerced to UTC, so a log-dive session that greps the
    backend log by a kanban-card timestamp silently misses by the
    local-UTC offset — see kanban card 72dc97c0… ("Dispatched debug-cards
    geven UTC-timestamps maar backend-logs zijn CEST — 2u offset
    misleidt log-diving"). Emitting the log timestamp in UTC removes the
    offset so card and log times can be grep'd directly.
    """
    converter = time.gmtime

    def formatTime(self, record, datefmt=None):
        # Always emit UTC ISO 8601 with millisecond precision. The caller's
        # ``datefmt`` is ignored — the formatter's only caller is
        # ``configure_logging`` below, which does not pass one, and the
        # shape we want (``...SSSZ``) needs both ``time.strftime`` for
        # the second-resolution body and ``record.msecs`` for the
        # fractional part.
        ct = self.converter(record.created)
        return (
            time.strftime("%Y-%m-%dT%H:%M:%S", ct)
            + f".{int(record.msecs):03d}Z"
        )


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger to emit JSON with correlation_id field."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_CorrelationIdFilter())
    formatter = _UtcJsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s %(correlation_id)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
