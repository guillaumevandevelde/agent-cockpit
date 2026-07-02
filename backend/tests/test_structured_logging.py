"""Tests for structured logging: JSON formatter and correlation ID middleware."""
import json
import logging
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

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
