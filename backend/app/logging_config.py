"""Structured JSON logging configuration with per-request correlation IDs."""
import logging
import sys
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


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger to emit JSON with correlation_id field."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_CorrelationIdFilter())
    formatter = JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s %(correlation_id)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
