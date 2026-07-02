"""Tests for the Host registry service.

Uses an in-memory SQLite database so tests never touch the production DB.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.database import Base
from app.models.host import Host  # noqa: F401 (register model)
from app.services.host_service import (
    HostNotFoundError,
    create_host,
    delete_host,
    get_host,
    list_hosts,
    update_host,
    _host_to_dict,
)

# In-memory engine shared across tests
_test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


@pytest_asyncio.fixture(autouse=True)
async def _reset_tables():
    """Drop and recreate all tables before each test."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db():
    """Provide a clean async session per test."""
    async with _test_session_factory() as session:
        yield session


SAMPLE_HOST = {
    "alias": "dev-server",
    "hostname": "192.168.1.100",
    "port": 22,
    "username": "admin",
    "ssh_key_path": "/home/user/.ssh/id_ed25519",
}


@pytest.mark.asyncio
async def test_create_host(db):
    host = await create_host(db, SAMPLE_HOST)
    assert host["id"] is not None
    assert host["alias"] == "dev-server"
    assert host["hostname"] == "192.168.1.100"
    assert host["port"] == 22
    assert host["username"] == "admin"
    assert host["ssh_key_path"] == "/home/user/.ssh/id_ed25519"
    assert host["status"] == "unknown"
    assert host["created_at"] is not None
    assert host["updated_at"] is not None


@pytest.mark.asyncio
async def test_create_host_default_port(db):
    data = {**SAMPLE_HOST, "port": 22}
    host = await create_host(db, data)
    assert host["port"] == 22


@pytest.mark.asyncio
async def test_create_host_duplicate_alias(db):
    await create_host(db, SAMPLE_HOST)
    with pytest.raises(Exception):  # SQLite UNIQUE constraint
        await create_host(db, SAMPLE_HOST)


@pytest.mark.asyncio
async def test_get_host(db):
    created = await create_host(db, SAMPLE_HOST)
    fetched = await get_host(db, created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["alias"] == SAMPLE_HOST["alias"]


@pytest.mark.asyncio
async def test_get_host_not_found(db):
    with pytest.raises(HostNotFoundError):
        await get_host(db, 999)


@pytest.mark.asyncio
async def test_list_hosts(db):
    assert await list_hosts(db) == []

    await create_host(db, SAMPLE_HOST)
    await create_host(db, {**SAMPLE_HOST, "alias": "staging"})
    hosts = await list_hosts(db)
    assert len(hosts) == 2


@pytest.mark.asyncio
async def test_update_host(db):
    created = await create_host(db, SAMPLE_HOST)
    updated = await update_host(db, created["id"], {"alias": "prod-server"})
    assert updated["alias"] == "prod-server"
    assert updated["hostname"] == SAMPLE_HOST["hostname"]

    # Verify the change is persisted
    fetched = await get_host(db, created["id"])
    assert fetched["alias"] == "prod-server"


@pytest.mark.asyncio
async def test_update_host_not_found(db):
    with pytest.raises(HostNotFoundError):
        await update_host(db, 999, {"alias": "ghost"})


@pytest.mark.asyncio
async def test_delete_host(db):
    created = await create_host(db, SAMPLE_HOST)
    await delete_host(db, created["id"])
    with pytest.raises(HostNotFoundError):
        await get_host(db, created["id"])


@pytest.mark.asyncio
async def test_delete_host_not_found(db):
    with pytest.raises(HostNotFoundError):
        await delete_host(db, 999)


@pytest.mark.asyncio
async def test_list_hosts_after_delete(db):
    h1 = await create_host(db, SAMPLE_HOST)
    h2 = await create_host(db, {**SAMPLE_HOST, "alias": "other"})
    assert len(await list_hosts(db)) == 2
    await delete_host(db, h1["id"])
    hosts = await list_hosts(db)
    assert len(hosts) == 1
    assert hosts[0]["id"] == h2["id"]


@pytest.mark.asyncio
async def test_host_to_dict_roundtrip(db):
    created = await create_host(db, SAMPLE_HOST)
    assert isinstance(created["id"], int)
    assert isinstance(created["created_at"], str)
    assert isinstance(created["updated_at"], str)


def test_host_to_dict_none_dates():
    """_host_to_dict should handle None dates gracefully."""
    host = Host(
        alias="test",
        hostname="localhost",
        port=22,
        username="user",
        status="unknown",
    )
    d = _host_to_dict(host)
    assert d["created_at"] is None
    assert d["updated_at"] is None


def test_host_to_dict_with_dates():
    from datetime import datetime, timezone
    dt = datetime.now(timezone.utc)
    host = Host(
        alias="test",
        hostname="localhost",
        port=22,
        username="user",
        status="unknown",
        created_at=dt,
        updated_at=dt,
    )
    d = _host_to_dict(host)
    assert d["created_at"] == dt.isoformat()
    assert d["updated_at"] == dt.isoformat()
