"""Edge cases for PluginService: install conflicts, marketplace timeouts,
duplicate/invalid marketplace input, and invalid plugin manifests."""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.database import Marketplace
from app.models.schemas import CLIResult, MarketplaceCreate, PluginInstallRequest
from app.services.plugin_service import PluginService


async def _seed_marketplace(db, name="mp", url="https://x/p.json"):
    # Insert directly via ORM: PluginService.add_marketplace's success path builds
    # a stale MarketplaceResponse shape, so seeding bypasses that unrelated path.
    db.add(Marketplace(name=name, url=url))
    await db.commit()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


class _FakeClient:
    """Async-context httpx client stub whose get() raises a chosen error."""
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        raise self._exc


def test_install_reports_failure_on_nonzero_exit(monkeypatch):
    # A CLI install that exits non-zero (e.g. plugin already installed / git
    # conflict) must surface success=False, not raise.
    svc = PluginService()
    monkeypatch.setattr(
        svc.cli_executor, "execute",
        lambda *a, **k: CLIResult(stdout="", stderr="already exists", exit_code=1),
    )
    resp = svc.install_plugin(PluginInstallRequest(name="dup-plugin"))
    assert resp.success is False
    assert "dup-plugin" in resp.message


def test_install_swallows_executor_exception(monkeypatch):
    svc = PluginService()
    def boom(*a, **k):
        raise RuntimeError("git crashed")
    monkeypatch.setattr(svc.cli_executor, "execute", boom)
    resp = svc.install_plugin(PluginInstallRequest(name="x"))
    assert resp.success is False
    assert "git crashed" in resp.stderr


def test_resolve_marketplace_input_rejects_bare_name():
    svc = PluginService()
    with pytest.raises(ValueError):
        svc._resolve_marketplace_input("justaname")


@pytest.mark.asyncio
async def test_add_marketplace_requires_db():
    svc = PluginService(db=None)
    with pytest.raises(ValueError):
        await svc.add_marketplace(MarketplaceCreate(name="m", url="https://x/p.json"))


@pytest.mark.asyncio
async def test_add_marketplace_rejects_duplicate_name(db):
    svc = PluginService(db=db)
    await _seed_marketplace(db, name="dup")
    with pytest.raises(ValueError, match="already exists"):
        await svc.add_marketplace(MarketplaceCreate(name="dup", url="https://y/p.json"))


@pytest.mark.asyncio
async def test_add_marketplace_requires_name_and_url(db):
    svc = PluginService(db=db)
    with pytest.raises(ValueError):
        await svc.add_marketplace(MarketplaceCreate())  # no name, url, or input


@pytest.mark.asyncio
async def test_sync_marketplace_returns_false_on_network_timeout(db, monkeypatch):
    svc = PluginService(db=db)
    await _seed_marketplace(db, name="mp")
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _FakeClient(httpx.TimeoutException("timed out")),
    )
    assert await svc.sync_marketplace("mp") is False


@pytest.mark.asyncio
async def test_sync_unknown_marketplace_returns_false(db):
    svc = PluginService(db=db)
    assert await svc.sync_marketplace("does-not-exist") is False


def test_validate_plugin_flags_invalid_manifest_json(tmp_path):
    plugin_dir = tmp_path / "plug"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text("{ not valid json ")
    result = PluginService().validate_plugin(str(plugin_dir))
    assert result.valid is False
    assert any("Invalid JSON" in e for e in result.errors)


def test_validate_plugin_flags_missing_manifest(tmp_path):
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    result = PluginService().validate_plugin(str(plugin_dir))
    assert result.valid is False
    assert any("plugin.json" in e for e in result.errors)


def test_validate_plugin_rejects_nonexistent_path(tmp_path):
    result = PluginService().validate_plugin(str(tmp_path / "nope"))
    assert result.valid is False
    assert any("does not exist" in e for e in result.errors)
