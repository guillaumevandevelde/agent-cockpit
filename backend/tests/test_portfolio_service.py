"""PortfolioService aggregation tests.

Fixture: 3 registered projects (1 meta + 2 product) plus one orphan board
(cards for a project_key with no registered Project row). Asserts per-project
column buckets, done_24h from the op-log, autodispatch flag, last_activity /
last_dispatch timestamps, and the portfolio-wide sum.
"""
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base, get_db
from app.kanban.models import KanbanCard, KanbanMeta, KanbanOp
from app.main import app
from app.models.database import Project
from app.services.portfolio_service import PortfolioService
from tests.kanban_test_db import TestSessionLocal

KanbanSessionLocal = TestSessionLocal()

# Isolated main-DB engine so the aggregate over *all* projects never sees the
# shared claude_registry.db (which other tests / real rows live in).
_fd, _main_db_path = tempfile.mkstemp(prefix="portfolio_main_", suffix=".db")
os.close(_fd)
_main_engine = create_async_engine(
    f"sqlite+aiosqlite:///{_main_db_path}", future=True, poolclass=NullPool
)
_MainSession = async_sessionmaker(_main_engine, class_=AsyncSession, expire_on_commit=False)

# Deterministic path -> project_key mapping (no git subprocess in tests).
KEYS = {
    "/repo/cockpit": "git:github.com/x/cockpit",
    "/repo/app-a": "git:github.com/x/app-a",
    "/repo/app-b": "git:github.com/x/app-b",
}
ORPHAN_KEY = "git:github.com/x/orphan"


def _resolver(path: str) -> str:
    return KEYS[path]


@pytest_asyncio.fixture
async def main_db():
    async with _main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with _MainSession() as s:
        yield s


def _card(cid: str, key: str, column: str) -> KanbanCard:
    return KanbanCard(id=cid, project_key=key, title=cid, column=column, rank=cid)


def _op(oid: str, entity_id: str, op_type: str, when: datetime, payload: dict) -> KanbanOp:
    return KanbanOp(
        op_id=oid, device_id="dev", seq=int(oid.rsplit(":", 1)[1]), hlc=oid,
        project_key="", entity_type="card", entity_id=entity_id,
        op_type=op_type, payload=payload, created_at=when,
    )


async def _seed(main: AsyncSession):
    main.add_all([
        Project(name="cockpit", path="/repo/cockpit", kind="meta"),
        Project(name="app-a", path="/repo/app-a", kind="product"),
        Project(name="app-b", path="/repo/app-b", kind="product"),
    ])
    await main.commit()

    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=2)
    async with KanbanSessionLocal() as k:
        ck = "git:github.com/x/cockpit"
        ak = "git:github.com/x/app-a"
        k.add_all([
            # cockpit: 2 backlog, 1 doing (agent column), 1 impediment
            _card("c1", ck, "Backlog"),
            _card("c2", ck, "Backlog"),
            _card("c3", ck, "engineer"),
            _card("c4", ck, "Impediment"),
            # app-a: 1 todo (To Resume), 1 done (in-window), 1 done (stale)
            _card("a1", ak, "To Resume"),
            _card("a2", ak, "Done"),
            _card("a3", ak, "Done"),
            # orphan board (no Project row): 1 backlog
            _card("o1", ORPHAN_KEY, "Backlog"),
        ])
        k.add_all([
            _op("dev:1", "c3", "claim", recent, {"claimed_by": "agent:sess-xyz"}),
            _op("dev:2", "c1", "comment", now, {"text": "hi"}),
            _op("dev:3", "a2", "move", recent, {"column": "Done"}),
            _op("dev:4", "a3", "move", old, {"column": "Done"}),
        ])
        # autodispatch enabled for app-a only
        k.add(KanbanMeta(key="autodispatch:git:github.com/x/app-a", value="1"))
        k.add(KanbanMeta(key="autodispatch:git:github.com/x/cockpit", value="0"))
        await k.commit()


@pytest.mark.asyncio
async def test_aggregate_per_project_and_totals(main_db):
    await _seed(main_db)
    async with KanbanSessionLocal() as k:
        overview = await PortfolioService(main_db, k).aggregate(key_resolver=_resolver)

    by_name = {p.name: p for p in overview.projects}
    assert set(by_name) == {"cockpit", "app-a", "app-b", ORPHAN_KEY}

    cockpit = by_name["cockpit"]
    assert cockpit.kind == "meta"
    assert cockpit.autodispatch_enabled is False
    assert cockpit.totals.backlog == 2
    assert cockpit.totals.doing == 1
    assert cockpit.totals.impediment == 1
    assert cockpit.totals.done_24h == 0
    assert cockpit.last_activity is not None
    assert cockpit.last_dispatch is not None  # agent claim on c3

    app_a = by_name["app-a"]
    assert app_a.kind == "product"
    assert app_a.autodispatch_enabled is True
    assert app_a.totals.todo == 1
    assert app_a.totals.done_24h == 1  # a2 recent; a3 stale (48h) excluded
    assert app_a.last_dispatch is None  # no agent claim

    app_b = by_name["app-b"]
    assert app_b.totals.backlog == 0
    assert app_b.last_activity is None

    orphan = by_name[ORPHAN_KEY]
    assert orphan.id is None
    assert orphan.kind == "unknown"
    assert orphan.totals.backlog == 1

    assert overview.totals.backlog == 3  # cockpit 2 + orphan 1
    assert overview.totals.doing == 1
    assert overview.totals.todo == 1
    assert overview.totals.impediment == 1
    assert overview.totals.done_24h == 1


@pytest.mark.asyncio
async def test_overview_endpoint(main_db, monkeypatch):
    await _seed(main_db)
    monkeypatch.setattr(
        "app.services.portfolio_service.resolve_project_key", _resolver
    )
    app.dependency_overrides[get_db] = lambda: main_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.get("/api/v1/portfolio/overview")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert {p["name"] for p in body["projects"]} == {
        "cockpit", "app-a", "app-b", ORPHAN_KEY
    }
    assert body["totals"]["backlog"] == 3
