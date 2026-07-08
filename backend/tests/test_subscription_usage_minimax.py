"""Mocked httpx tests for MinimaxUsageProvider."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscriptions.base import SubscriptionUsageSnapshot
from app.services.subscriptions.minimax import MinimaxUsageProvider


class _FakeAsyncClient:
    """Tiny stand-in for httpx.AsyncClient that returns a queued response."""

    def __init__(self, *args, **kwargs):
        self._responses = []
        self.calls = []

    def add_responses(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status, body = self._responses.pop(0)
        return _FakeResponse(status, body)


def _make_fake(responses):
    """Build a callable that mimics httpx.AsyncClient(timeout=...) and returns a fake."""
    fake = _FakeAsyncClient()
    fake.add_responses(responses)
    return fake


class _FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        import json
        return json.loads(self._body)

    @property
    def text(self) -> str:
        return self._body


@pytest.mark.asyncio
async def test_happy_path_maps_periods(monkeypatch):
    fake = _make_fake([(200, '[{"label":"5h","used":10,"limit":100,"unit":"tokens","reset_at":"2026-07-09T00:00:00Z"}]')])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda *a, **kw: fake)
    p = MinimaxUsageProvider()
    snap = await p.get_snapshot()
    assert snap.error_code is None
    assert len(snap.periods) == 1
    period = snap.periods[0]
    assert period.label == "5h"
    assert period.used == 10
    assert period.limit == 100
    assert period.unit == "tokens"
    assert period.source == "api"


@pytest.mark.asyncio
async def test_unauthorized_returns_unauthorized(monkeypatch):
    fake = _make_fake([(401, '{"error":"bad key"}')])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda *a, **kw: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "unauthorized"
    assert snap.periods == ()


@pytest.mark.asyncio
async def test_5xx_returns_unreachable(monkeypatch):
    fake = _make_fake([(503, "upstream down")])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda *a, **kw: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "unreachable"


@pytest.mark.asyncio
async def test_malformed_json_returns_malformed(monkeypatch):
    fake = _make_fake([(200, "not json")])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda *a, **kw: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "malformed"


@pytest.mark.asyncio
async def test_no_endpoint_returns_no_endpoint_when_url_404s(monkeypatch):
    """If every candidate 404s, the provider returns no_endpoint."""
    fake = _make_fake([(404, "not found")] * 8)
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda *a, **kw: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "no_endpoint"
