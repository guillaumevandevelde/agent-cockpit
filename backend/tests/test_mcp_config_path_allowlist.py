"""Path-allowlist for project-scoped ``.mcp.json`` writes (kanban card I4b).

``MCPConfigService._write_project_mcp_config`` must refuse to write into a
``project_path`` that is not registered in the ``projects`` table, so an
unauthenticated API caller cannot make the server write config to an arbitrary
filesystem location. Read paths are intentionally unaffected.

These tests hit the real app DB (``claude_registry.db``) rather than an isolated
test DB, because the allowlist is a lookup against the actual ``projects`` table
(see ``test_mcp_server.py`` for the same pattern); each test cleans up its row.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal, Base, engine
from app.main import app
from app.models.database import Project
from app.models.schemas import MCPServerCreate
from app.services.mcp_config_service import UnregisteredProjectPathError
from app.services.mcp_service import MCPService


async def _ensure_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _register_project(path: str) -> int:
    await _ensure_tables()
    async with AsyncSessionLocal() as db:
        proj = Project(name="i4b-allowlist-test", path=path, is_active=False)
        db.add(proj)
        await db.commit()
        return proj.id


async def _delete_project(project_id: int) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(Project, project_id)
        if row:
            await db.delete(row)
            await db.commit()


def _server() -> MCPServerCreate:
    return MCPServerCreate(name="demo", type="stdio", scope="project", command="echo")


@pytest.mark.asyncio
async def test_registered_path_writes(tmp_path):
    """A path present in the projects table writes .mcp.json as before."""
    proj_dir = tmp_path / "registered"
    proj_dir.mkdir()
    project_id = await _register_project(str(proj_dir))
    try:
        async with AsyncSessionLocal() as db:
            await MCPService().add_server(_server(), str(proj_dir), db)
        data = json.loads((proj_dir / ".mcp.json").read_text())
        assert "demo" in data["mcpServers"]
    finally:
        await _delete_project(project_id)


@pytest.mark.asyncio
async def test_unregistered_path_refused(tmp_path):
    """An unregistered path raises and writes nothing."""
    proj_dir = tmp_path / "rogue"
    proj_dir.mkdir()
    await _ensure_tables()
    async with AsyncSessionLocal() as db:
        with pytest.raises(UnregisteredProjectPathError):
            await MCPService().add_server(_server(), str(proj_dir), db)
    assert not (proj_dir / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_race_path_deleted_before_write(tmp_path):
    """Path registered then removed just before the write fails cleanly."""
    proj_dir = tmp_path / "gone"
    proj_dir.mkdir()
    project_id = await _register_project(str(proj_dir))
    await _delete_project(project_id)  # deregistered after the caller resolved it
    async with AsyncSessionLocal() as db:
        with pytest.raises(UnregisteredProjectPathError):
            await MCPService().add_server(_server(), str(proj_dir), db)
    assert not (proj_dir / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_none_path_allowed(tmp_path, monkeypatch):
    """project_path=None falls back to cwd (server-controlled) and is allowed."""
    monkeypatch.chdir(tmp_path)
    await _ensure_tables()
    async with AsyncSessionLocal() as db:
        await MCPService().add_server(_server(), None, db)
    assert (tmp_path / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_api_returns_403_for_unregistered_path(tmp_path):
    """The create endpoint maps an unregistered path to 403, not 500."""
    proj_dir = tmp_path / "api-rogue"
    proj_dir.mkdir()
    await _ensure_tables()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/mcp/servers",
            params={"project_path": str(proj_dir)},
            json={"name": "demo", "type": "stdio", "scope": "project", "command": "echo"},
        )
    assert resp.status_code == 403
    assert "not a registered project" in resp.json()["detail"]
    assert not (proj_dir / ".mcp.json").exists()
