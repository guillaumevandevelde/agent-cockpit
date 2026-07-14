"""Tests for structured logging: JSON formatter and correlation ID middleware."""
import json
import logging
import uuid
from datetime import UTC

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.logging_config import configure_logging, get_correlation_id, set_correlation_id
from app.middleware.correlation_id import CorrelationIdMiddleware


@pytest.fixture
def test_app():
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"cid": get_correlation_id()}

    return app


@pytest.mark.asyncio
async def test_correlation_id_generated_when_absent(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/ping")
    assert response.status_code == 200
    cid = response.headers.get("x-correlation-id")
    assert cid is not None
    uuid.UUID(cid)  # must be a valid UUID


@pytest.mark.asyncio
async def test_correlation_id_propagated_from_request(test_app):
    sent_cid = str(uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/ping", headers={"X-Correlation-ID": sent_cid})
    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == sent_cid
    assert response.json()["cid"] == sent_cid


@pytest.mark.asyncio
async def test_correlation_id_accessible_in_handler(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/ping")
    body = response.json()
    header_cid = response.headers.get("x-correlation-id")
    assert body["cid"] == header_cid


def test_json_formatter_emits_valid_json(capfd):
    configure_logging()
    logger = logging.getLogger("test.json_formatter")
    logger.info("hello world")
    out, _ = capfd.readouterr()
    # Each log line must be parseable JSON
    for line in out.strip().splitlines():
        parsed = json.loads(line)
        assert "message" in parsed
        assert "level" in parsed
        assert "timestamp" in parsed
        assert "correlation_id" in parsed


def test_correlation_id_injected_into_log_record(capfd):
    configure_logging()
    test_cid = str(uuid.uuid4())
    set_correlation_id(test_cid)
    logger = logging.getLogger("test.cid_inject")
    logger.info("check cid")
    out, _ = capfd.readouterr()
    for line in out.strip().splitlines():
        parsed = json.loads(line)
        if parsed.get("message") == "check cid":
            assert parsed["correlation_id"] == test_cid
            return
    pytest.fail("Expected log line not found")


def test_json_formatter_emits_utc_iso_timestamp(capfd):
    """``timestamp`` must be UTC ISO 8601 with ``Z`` suffix so log-dive
    sessions can grep logs by kanban-card UTC timestamp without applying
    a local-time offset (host is CEST / UTC+2 — see kanban card
    ``72dc97c0…``: "Dispatched debug-cards geven UTC-timestamps maar
    backend-logs zijn CEST — 2u offset misleidt log-diving").
    """
    import re
    from datetime import datetime, timedelta

    configure_logging()
    logger = logging.getLogger("test.utc_format")
    logger.info("utc ts")
    out, _ = capfd.readouterr()

    pattern = re.compile(
        r'"timestamp":\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)"'
    )
    matched = pattern.findall(out)
    assert matched, f"No UTC ISO 8601 timestamp found in: {out!r}"

    ts = datetime.strptime(matched[-1], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=UTC
    )
    now = datetime.now(UTC)
    # Allow a few seconds for test execution; if the formatter were still
    # emitting local time tagged with ``Z`` the delta would be ~2h.
    assert abs((now - ts).total_seconds()) < 5, (
        f"timestamp {ts} not within 5s of UTC now {now} — "
        "formatter likely still emits local time"
    )
    # Belt-and-suspenders: explicitly refute the "local-time + Z" failure
    # mode. If the test ran at 14:00 CEST and a buggy formatter wrote
    # local time with a ``Z``, ``ts`` would land at 14:00 UTC vs real
    # 12:00 UTC, a 2h delta.
    local_offset = timedelta(hours=2)  # CEST in summer
    fake_local_as_utc = now + local_offset
    assert abs((fake_local_as_utc - ts).total_seconds()) > 60 * 30, (
        f"timestamp {ts} is too close to local-time-as-UTC "
        f"({fake_local_as_utc}) — formatter probably mis-tagging local time"
    )
