"""Shared in-memory kanban DB for tests.

Single source of truth — both conftest.py and test files import from here.
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession, create_async_engine, async_sessionmaker,
)

from app.kanban.db import KanbanBase

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
_test_session_factory = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


class TestSessionLocal:
    def __call__(self):
        return _test_session_factory()


async def reset_test_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
