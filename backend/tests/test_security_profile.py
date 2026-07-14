"""Tests for the per-project security profile.

Covers the default policy for a new product project, the REST CRUD contract,
PUT idempotency, and the audit log on a risk_class transition. The security
audit sink writes both a structured row to the ``security_audit`` table and
the legacy ``logging`` line — see ``veilig-bouwen-en-uitleveren.md`` §4.8.
"""
import logging

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

# Import every model so ``Base.metadata.create_all`` knows about every
# table — each test fixture spins up an in-memory SQLite, and a missing
# import means a missing table.
from app.models import security_audit as _security_audit_model  # noqa: F401
from app.models.security_profile import (
    DEFAULT_PRODUCT_RESOURCE_QUOTA,
)
from app.services.security_profile_service import SecurityProfileService

# ---------------------------------------------------------------- helpers


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Isolated in-memory SQLite session so we never touch the real DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------- schema layer


def test_default_product_resource_quota_has_expected_keys():
    assert set(DEFAULT_PRODUCT_RESOURCE_QUOTA) == {"memory_mb", "cpu_quota", "pids_limit", "disk_mb"}
    for v in DEFAULT_PRODUCT_RESOURCE_QUOTA.values():
        assert isinstance(v, int) and v > 0


# ---------------------------------------------------------------- service layer


@pytest.mark.asyncio
async def test_default_policy_for_new_product_project(db_session):
    svc = SecurityProfileService(db_session)
    profile = await svc.get_or_create_for_project("/tmp/product-a", project_kind="product")

    assert profile.risk_class == "product-staging"
    assert profile.default_skip_permissions is False
    assert profile.default_transport == "sandcastle"
    assert profile.network_policy == "allowlist"
    assert profile.egress_allowlist == []
    assert profile.resource_quota == DEFAULT_PRODUCT_RESOURCE_QUOTA
    assert profile.secrets_scope_id is None


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_row(db_session):
    svc = SecurityProfileService(db_session)
    first = await svc.get_or_create_for_project("/tmp/product-a", project_kind="product")
    second = await svc.get_or_create_for_project("/tmp/product-a", project_kind="product")
    assert first.id == second.id


@pytest.mark.asyncio
async def test_get_unknown_project_returns_none(db_session):
    svc = SecurityProfileService(db_session)
    assert await svc.get("/tmp/missing") is None


@pytest.mark.asyncio
async def test_upsert_replaces_full_record_idempotent(db_session):
    svc = SecurityProfileService(db_session)
    # PUT = replace-all: same payload twice yields equivalent row.
    payload = {
        "risk_class": "product-prod",
        "default_transport": "sandcastle",
        "default_skip_permissions": False,
        "secrets_scope_id": "scope-a",
        "resource_quota": {"memory_mb": 2048, "cpu_quota": 2, "pids_limit": 256, "disk_mb": 5120},
        "network_policy": "allowlist",
        "egress_allowlist": ["pypi.org", "github.com"],
    }
    a = await svc.upsert("/tmp/product-a", payload)
    b = await svc.upsert("/tmp/product-a", payload)
    assert a.id == b.id
    assert a.risk_class == "product-prod"
    assert a.egress_allowlist == ["pypi.org", "github.com"]
    assert a.resource_quota["memory_mb"] == 2048


@pytest.mark.asyncio
async def test_patch_merges_partial_fields(db_session):
    svc = SecurityProfileService(db_session)
    await svc.upsert(
        "/tmp/product-a",
        {
            "risk_class": "product-staging",
            "default_transport": "sandcastle",
            "default_skip_permissions": False,
            "secrets_scope_id": None,
            "resource_quota": DEFAULT_PRODUCT_RESOURCE_QUOTA,
            "network_policy": "allowlist",
            "egress_allowlist": [],
        },
    )
    patched = await svc.patch(
        "/tmp/product-a",
        {
            "risk_class": "product-prod",
            "egress_allowlist": ["pypi.org"],
        },
    )
    assert patched.risk_class == "product-prod"
    assert patched.egress_allowlist == ["pypi.org"]
    # Untouched fields stay
    assert patched.default_transport == "sandcastle"
    assert patched.network_policy == "allowlist"


@pytest.mark.asyncio
async def test_patch_missing_returns_none(db_session):
    svc = SecurityProfileService(db_session)
    assert await svc.patch("/tmp/missing", {"risk_class": "meta"}) is None


@pytest.mark.asyncio
async def test_delete_removes_row(db_session):
    svc = SecurityProfileService(db_session)
    await svc.upsert(
        "/tmp/product-a",
        {
            "risk_class": "product-staging",
            "default_transport": "sandcastle",
            "default_skip_permissions": False,
            "secrets_scope_id": None,
            "resource_quota": DEFAULT_PRODUCT_RESOURCE_QUOTA,
            "network_policy": "allowlist",
            "egress_allowlist": [],
        },
    )
    assert await svc.delete("/tmp/product-a") is True
    assert await svc.delete("/tmp/product-a") is False
    assert await svc.get("/tmp/product-a") is None


@pytest.mark.asyncio
async def test_risk_class_transition_logs_audit_event(db_session, caplog):
    svc = SecurityProfileService(db_session)
    await svc.upsert(
        "/tmp/product-a",
        {
            "risk_class": "product-staging",
            "default_transport": "sandcastle",
            "default_skip_permissions": False,
            "secrets_scope_id": None,
            "resource_quota": DEFAULT_PRODUCT_RESOURCE_QUOTA,
            "network_policy": "allowlist",
            "egress_allowlist": [],
        },
    )

    caplog.set_level(logging.INFO, logger="app.services.security_profile_service")
    await svc.patch(
        "/tmp/product-a",
        {"risk_class": "product-prod"},
    )

    audit_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "risk_class_transition" in rec.getMessage()
    ]
    assert audit_lines, "expected an audit log line for risk_class transition"
    assert "product-staging" in audit_lines[0]
    assert "product-prod" in audit_lines[0]


@pytest.mark.asyncio
async def test_no_audit_when_risk_class_unchanged(db_session, caplog):
    svc = SecurityProfileService(db_session)
    await svc.upsert(
        "/tmp/product-a",
        {
            "risk_class": "product-staging",
            "default_transport": "sandcastle",
            "default_skip_permissions": False,
            "secrets_scope_id": None,
            "resource_quota": DEFAULT_PRODUCT_RESOURCE_QUOTA,
            "network_policy": "allowlist",
            "egress_allowlist": [],
        },
    )

    caplog.set_level(logging.INFO, logger="app.services.security_profile_service")
    await svc.patch("/tmp/product-a", {"egress_allowlist": ["pypi.org"]})

    audit_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "risk_class_transition" in rec.getMessage()
    ]
    assert audit_lines == []


# ---------------------------------------------------------------- API layer


def _override_db(session):
    async def _get_db():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return _get_db


@pytest.mark.asyncio
async def test_api_get_default_creates_profile_for_product_path(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        async with _client() as ac:
            r = await ac.get("/api/v1/security/profiles", params={"project_path": "/tmp/product-a"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["project_path"] == "/tmp/product-a"
        assert body["risk_class"] == "product-staging"
        assert body["default_transport"] == "sandcastle"
        assert body["network_policy"] == "allowlist"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_api_put_is_idempotent(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    payload = {
        "risk_class": "product-prod",
        "default_transport": "sandcastle",
        "default_skip_permissions": False,
        "secrets_scope_id": "scope-a",
        "resource_quota": {"memory_mb": 1024, "cpu_quota": 1, "pids_limit": 128, "disk_mb": 2048},
        "network_policy": "allowlist",
        "egress_allowlist": ["pypi.org"],
    }
    try:
        async with _client() as ac:
            r1 = await ac.put(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
                json=payload,
            )
            r2 = await ac.put(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
                json=payload,
            )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["project_path"] == r2.json()["project_path"]
        assert r1.json()["resource_quota"] == r2.json()["resource_quota"]
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_api_patch_and_delete_round_trip(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        async with _client() as ac:
            r = await ac.get(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
            )
            assert r.status_code == 200
            r = await ac.patch(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
                json={"risk_class": "product-prod"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["risk_class"] == "product-prod"
            r = await ac.delete(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
            )
            assert r.status_code == 200
            r = await ac.get(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
            )
            # default-policy recreate-after-delete: still returns the default row.
            assert r.status_code == 200
            assert r.json()["risk_class"] == "product-staging"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_api_rejects_invalid_risk_class(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        async with _client() as ac:
            r = await ac.patch(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
                json={"risk_class": "bogus"},
            )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_api_rejects_invalid_network_policy(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        async with _client() as ac:
            r = await ac.patch(
                "/api/v1/security/profiles",
                params={"project_path": "/tmp/product-a"},
                json={"network_policy": "wide-open"},
            )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)