"""Portfolio migration classification-pass tests.

Fixture: 3 registered projects (the cockpit checkout + 2 product apps) and one
product app whose key is force-listed via the override. Asserts the read-only
classification (no ``projects.kind`` write), the ``[portfolio-migration]`` audit
comment on the oldest open card, idempotency on re-run, and the endpoint.
"""
import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base, get_db
from app.kanban.models import KanbanCard, KanbanOp
from app.main import app
from app.models.database import Project
from app.services.portfolio_migration import (
    MIGRATION_COMMENT_PREFIX,
    classify_projects,
    run_migration_pass,
)
from tests.kanban_test_db import TestSessionLocal

KanbanSessionLocal = TestSessionLocal()

_fd, _main_db_path = tempfile.mkstemp(prefix="portfolio_mig_main_", suffix=".db")
os.close(_fd)
_main_engine = create_async_engine(
    f"sqlite+aiosqlite:///{_main_db_path}", future=True, poolclass=NullPool
)
_MainSession = async_sessionmaker(_main_engine, class_=AsyncSession, expire_on_commit=False)

COCKPIT_PATH = "/repo/cockpit"
COCKPIT_KEY = "git:github.com/x/cockpit"
KEYS = {
    COCKPIT_PATH: COCKPIT_KEY,
    "/repo/app-a": "git:github.com/x/app-a",
    "/repo/app-b": "git:github.com/x/app-b",
}


def _resolver(path: str) -> str:
    return KEYS[path]


@pytest_asyncio.fixture
async def main_db():
    async with _main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with _MainSession() as s:
        yield s


def _card(cid: str, key: str, column: str, created_at: datetime) -> KanbanCard:
    return KanbanCard(
        id=cid, project_key=key, title=cid, column=column, rank=cid,
        created_at=created_at,
    )


async def _seed(main: AsyncSession):
    # All three projects default to `product` (the server_default) — the pass
    # must propose meta for the cockpit checkout without any pre-set kind.
    main.add_all([
        Project(name="cockpit", path=COCKPIT_PATH),
        Project(name="app-a", path="/repo/app-a"),
        Project(name="app-b", path="/repo/app-b"),
    ])
    await main.commit()

    now = datetime.now(UTC)
    async with KanbanSessionLocal() as k:
        k.add_all([
            # cockpit board: a Done card (must be ignored) + two open cards.
            _card("c-done", COCKPIT_KEY, "Done", now - timedelta(hours=5)),
            _card("c-old", COCKPIT_KEY, "Backlog", now - timedelta(hours=3)),
            _card("c-new", COCKPIT_KEY, "engineer", now - timedelta(hours=1)),
            # app-a board: one open card (used by the override case).
            _card("a1", "git:github.com/x/app-a", "Backlog", now - timedelta(hours=2)),
        ])
        await k.commit()


@pytest.mark.asyncio
async def test_classify_proposes_meta_for_cockpit_only(main_db):
    await _seed(main_db)
    async with KanbanSessionLocal() as k:
        candidates = await classify_projects(
            main_db, k,
            cockpit_checkout_path=COCKPIT_PATH,
            extra_meta_keys=[],
            key_resolver=_resolver,
        )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.project_name == "cockpit"
    assert c.project_key == COCKPIT_KEY
    assert c.current_kind == "product"
    assert c.derived_kind == "meta"
    assert c.evidence == "remote-match"
    assert c.open_cards == 2  # c-old + c-new; c-done excluded

    # Read-only: no projects.kind write happened.
    row = (await main_db.execute(select(Project).where(Project.name == "cockpit"))).scalar_one()
    assert row.kind == "product"


@pytest.mark.asyncio
async def test_override_adds_config_meta_candidate(main_db):
    await _seed(main_db)
    async with KanbanSessionLocal() as k:
        candidates = await classify_projects(
            main_db, k,
            cockpit_checkout_path=COCKPIT_PATH,
            extra_meta_keys=["git:github.com/x/app-a"],
            key_resolver=_resolver,
        )

    by_name = {c.project_name: c for c in candidates}
    assert set(by_name) == {"cockpit", "app-a"}
    assert by_name["cockpit"].evidence == "remote-match"
    assert by_name["app-a"].evidence == "config-override"
    assert by_name["app-a"].derived_kind == "meta"


@pytest.mark.asyncio
async def test_already_meta_is_not_a_candidate(main_db):
    await _seed(main_db)
    # Flip cockpit to meta up front — a matching, already-tagged project must
    # produce no proposal (derived == current).
    row = (await main_db.execute(select(Project).where(Project.name == "cockpit"))).scalar_one()
    row.kind = "meta"
    await main_db.commit()

    async with KanbanSessionLocal() as k:
        candidates = await classify_projects(
            main_db, k,
            cockpit_checkout_path=COCKPIT_PATH,
            extra_meta_keys=[],
            key_resolver=_resolver,
        )
    assert candidates == []


@pytest.mark.asyncio
async def test_run_pass_posts_idempotent_comment(main_db):
    await _seed(main_db)
    async with KanbanSessionLocal() as k:
        candidates = await run_migration_pass(
            main_db, k,
            cockpit_checkout_path=COCKPIT_PATH,
            extra_meta_keys=[],
            key_resolver=_resolver,
        )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.comment_posted is True
    assert c.comment_card_id == "c-old"  # oldest OPEN card, not c-done

    async with KanbanSessionLocal() as k:
        comments = (
            await k.execute(
                select(KanbanOp.payload).where(
                    KanbanOp.entity_id == "c-old", KanbanOp.op_type == "comment"
                )
            )
        ).all()
    texts = [p["text"] for (p,) in comments]
    migration = [t for t in texts if t.startswith(MIGRATION_COMMENT_PREFIX)]
    assert len(migration) == 1
    assert "kind=meta" in migration[0]

    # Second run: same derived kind already proposed → no new comment.
    async with KanbanSessionLocal() as k:
        again = await run_migration_pass(
            main_db, k,
            cockpit_checkout_path=COCKPIT_PATH,
            extra_meta_keys=[],
            key_resolver=_resolver,
        )
    assert again[0].comment_posted is False
    assert again[0].comment_card_id == "c-old"

    async with KanbanSessionLocal() as k:
        comments = (
            await k.execute(
                select(KanbanOp.payload).where(
                    KanbanOp.entity_id == "c-old", KanbanOp.op_type == "comment"
                )
            )
        ).all()
    migration = [
        p["text"] for (p,) in comments if p["text"].startswith(MIGRATION_COMMENT_PREFIX)
    ]
    assert len(migration) == 1  # still exactly one


@pytest.mark.asyncio
async def test_migration_pass_endpoint(main_db, monkeypatch):
    await _seed(main_db)
    monkeypatch.setattr(
        "app.services.portfolio_migration.resolve_project_key", _resolver
    )
    monkeypatch.setattr(
        "app.services.portfolio_migration.PROJECT_ROOT", COCKPIT_PATH
    )
    app.dependency_overrides[get_db] = lambda: main_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post("/api/v1/portfolio/migration-pass")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["project_name"] == "cockpit"
    assert body[0]["derived_kind"] == "meta"
    assert body[0]["comment_posted"] is True
