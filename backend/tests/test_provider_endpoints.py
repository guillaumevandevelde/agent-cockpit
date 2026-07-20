"""Tests for the project-scoped Anthropic-compatible endpoint registry."""
import json

import pytest
import pytest_asyncio

from app.kanban.models import KanbanMeta
from app.services.agentic_cli.endpoints import (
    ENDPOINT_PREFIX,
    Endpoint,
    delete_endpoint,
    deserialize_endpoint,
    get_endpoint,
    list_endpoints,
    serialize_endpoint,
    upsert_endpoint,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

SessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _session():
    return SessionLocal()


# ---- pure (no DB) ---------------------------------------------------------


def test_serialize_roundtrip():
    ep = Endpoint(
        name="groq",
        base_url="https://api.groq.com/anthropic",
        model="llama-3.3-70b",
        credential_name="groq-key",
    )
    assert deserialize_endpoint(serialize_endpoint(ep)) == ep


def test_serialize_roundtrip_without_credential_name():
    ep = Endpoint(
        name="local-router",
        base_url="http://localhost:20128/v1",
        model="claude-sonnet-4",
    )
    assert deserialize_endpoint(serialize_endpoint(ep)).credential_name is None


def test_serialize_rejects_empty_base_url():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="x", base_url="   ", model="m"))


def test_serialize_rejects_empty_model():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="x", base_url="https://api.example.com", model=""))


def test_serialize_rejects_invalid_name():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="Invalid Name!", base_url="u", model="m"))


def test_serialize_rejects_newline_in_base_url():
    with pytest.raises(ValueError):
        serialize_endpoint(
            Endpoint(name="x", base_url="https://api.example.com\nFOO=bar", model="m"),
        )


def test_deserialize_corrupt_returns_none():
    assert deserialize_endpoint("not-json") is None


def test_deserialize_wrong_shape_returns_none():
    assert deserialize_endpoint(json.dumps([1, 2, 3])) is None


def test_deserialize_missing_field_returns_none():
    assert deserialize_endpoint(json.dumps({"name": "x", "base_url": "u"})) is None


def test_deserialize_tolerates_unknown_keys():
    raw = json.dumps({
        "name": "x", "base_url": "https://api.example.com", "model": "m",
        "future_field": 42,
    })
    ep = deserialize_endpoint(raw)
    assert ep is not None
    assert ep.name == "x"


# ---- DB-backed ------------------------------------------------------------


async def test_list_endpoints_empty_when_nothing_stored():
    async with _session() as s:
        assert await list_endpoints(s, "proj-a") == []


async def test_upsert_then_get_roundtrips():
    ep = Endpoint(
        name="groq",
        base_url="https://api.groq.com/anthropic",
        model="llama-3.3-70b",
        credential_name="groq-key",
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await s.commit()
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "groq") == ep


async def test_upsert_overwrites_existing_row():
    ep = Endpoint(name="x", base_url="https://old.example.com", model="m")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await s.commit()
    ep_v2 = Endpoint(name="x", base_url="https://new.example.com", model="m2")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep_v2)
        await s.commit()
    async with _session() as s:
        rows = (await s.execute(
            __import__("sqlalchemy").select(KanbanMeta).where(
                KanbanMeta.key == f"{ENDPOINT_PREFIX}proj-a:x"
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == serialize_endpoint(ep_v2)


async def test_list_endpoints_returns_sorted_and_skips_corrupt():
    async with _session() as s:
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="zeta", base_url="https://z.example.com", model="zm"),
        )
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="alpha", base_url="https://a.example.com", model="am"),
        )
        # Inject a corrupt row that list_endpoints must skip, not raise on.
        s.add(KanbanMeta(
            key=f"{ENDPOINT_PREFIX}proj-a:corrupt", value="not-json",
        ))
        await s.commit()

    async with _session() as s:
        eps = await list_endpoints(s, "proj-a")
    assert [e.name for e in eps] == ["alpha", "zeta"]


async def test_list_endpoints_isolates_by_project():
    async with _session() as s:
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="x", base_url="https://a.example.com", model="ma"),
        )
        await upsert_endpoint(
            s, "proj-b",
            Endpoint(name="x", base_url="https://b.example.com", model="mb"),
        )
        await s.commit()
    async with _session() as s:
        a = await list_endpoints(s, "proj-a")
    async with _session() as s:
        b = await list_endpoints(s, "proj-b")
    assert len(a) == 1 and a[0].base_url == "https://a.example.com"
    assert len(b) == 1 and b[0].base_url == "https://b.example.com"


async def test_delete_endpoint_is_idempotent():
    async with _session() as s:
        # Missing row: no-op, no exception.
        await delete_endpoint(s, "proj-a", "absent")
        await s.commit()
    ep = Endpoint(name="x", base_url="https://x.example.com", model="m")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await delete_endpoint(s, "proj-a", "x")
        await s.commit()
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "x") is None
    # Second delete is still a no-op.
    async with _session() as s:
        await delete_endpoint(s, "proj-a", "x")
        await s.commit()


async def test_get_endpoint_returns_none_when_absent():
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "nope") is None