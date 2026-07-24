"""REST API tests for the per-project token-saver kill-switch.

Covers ``GET /api/v1/kanban/token-saver`` and
``POST /api/v1/kanban/token-saver`` (kaart c31333bf…,
docs/superpowers/specs/2026-07-24-token-saver-integration-design.md §7.2).
The dispatcher reads the kill-switch on every spawn tick, so the
endpoints are the operator-facing override that switches RTK off
project-wide without touching the per-lane column flags.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanMeta
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def test_get_token_saver_returns_false_when_unset(client):
    """No row in ``KanbanMeta`` → ``enabled=False``."""
    r = client.get("/api/v1/kanban/token-saver", params={"project_key": "PROJ"})
    assert r.status_code == 200, r.text
    assert r.json() == {"project_key": "PROJ", "enabled": False}


def test_post_token_saver_persists_value(client):
    """POST writes ``"1"`` to ``KanbanMeta`` and GET returns it."""
    r = client.post(
        "/api/v1/kanban/token-saver",
        json={"project_key": "PROJ", "enabled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"project_key": "PROJ", "enabled": True}

    r = client.get(
        "/api/v1/kanban/token-saver", params={"project_key": "PROJ"},
    )
    assert r.json()["enabled"] is True


def test_post_token_saver_round_trips_false(client):
    """POST ``enabled=False`` after a true → GET returns ``False``."""
    client.post(
        "/api/v1/kanban/token-saver",
        json={"project_key": "PROJ", "enabled": True},
    )
    client.post(
        "/api/v1/kanban/token-saver",
        json={"project_key": "PROJ", "enabled": False},
    )
    r = client.get(
        "/api/v1/kanban/token-saver", params={"project_key": "PROJ"},
    )
    assert r.json()["enabled"] is False


def test_post_token_saver_value_is_canonical(client):
    """The persisted value is the literal string ``"1"`` / ``"0"`` —
    matches the convention used by ``set_autodispatch`` and
    ``set_skip_permissions`` so the kill-switch reads predictably
    from any other inspection path (e.g. an ad-hoc ``sqlite3``).
    """
    client.post(
        "/api/v1/kanban/token-saver",
        json={"project_key": "PROJ", "enabled": True},
    )

    async def _read():
        async with KanbanSessionLocal() as s:
            return (await s.execute(
                __import__("sqlalchemy").select(KanbanMeta).where(
                    KanbanMeta.key == "token_saver:PROJ",
                )
            )).scalar_one_or_none()

    import asyncio
    row = asyncio.run(_read())
    assert row is not None
    assert row.value == "1"
