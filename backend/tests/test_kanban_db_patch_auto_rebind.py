"""Regression test for ``conftest._patch_kanban_db`` auto-rebinding.

Self-improve kanban card 07d95f2c: the conftest used to hard-code an allow-list
of known ``KanbanSessionLocal`` consumers — every new module that imported it
silently kept the production reference until the first failing test surfaced
it (which is exactly what happened when kanban card 727470a8 added
``/api/v1/plans``).

This test file IS a ``KanbanSessionLocal`` consumer that the conftest's
allow-list never knew about. Its top-level import matches the production
pattern, so the conftest's ``_patch_kanban_db`` fixture has to find it purely
by scanning ``sys.modules`` and rebinding by identity. If the allow-list
approach ever regresses, this test catches it on the next run — and crucially,
it does so WITHOUT requiring a follow-up conftest edit (the acceptance
criterion: "adding a 5th module that imports ``KanbanSessionLocal`` requires
zero conftest changes").
"""
import pytest

# The import under test: this mirrors the production pattern
# (``from app.kanban.db import KanbanSessionLocal`` at module level). It binds
# the prod factory into this test module's ``__dict__`` at import time, before
# the conftest's session-scoped fixture runs. The fixture is expected to scan
# ``sys.modules`` and rebind the attribute to the test factory.
from app.kanban.db import KanbanSessionLocal  # noqa: F401


@pytest.mark.asyncio
async def test_module_level_kanban_session_local_uses_test_engine():
    """The rebound factory must produce a session bound to ``test_engine``,
    not the production kanban engine.

    If the conftest regressed to the old allow-list approach, this module's
    ``KanbanSessionLocal`` attribute would still be the prod factory and the
    session's ``.bind`` would point at the prod engine instead of
    ``test_engine`` — failing this assertion.
    """
    from tests.kanban_test_db import test_engine

    session = KanbanSessionLocal()
    try:
        assert session.bind is test_engine, (
            "KanbanSessionLocal() returned a session bound to "
            f"{session.bind!r}, expected the test engine {test_engine!r}. "
            "The conftest's _patch_kanban_db fixture did not rebind this "
            "module's import — the prod reference is still in place."
        )
    finally:
        await session.close()


def test_module_level_kanban_session_local_is_test_factory():
    """Belt-and-braces identity check: our module's ``KanbanSessionLocal``
    attribute must BE the conftest's ``_test_sf`` instance (not just any
    factory that happens to bind to test_engine). Catches regressions where
    someone replaces the rebind with a parallel factory instead of reusing
    the conftest's canonical one.
    """
    import tests.conftest as _conftest

    # The conftest's ``_test_sf = TestSessionLocal()`` is the single canonical
    # test factory. If this identity check fails, the rebind landed on a
    # different object (likely because TestSessionLocal() was instantiated
    # twice and the modules were rebound to separate instances, which would
    # fragment test cleanup semantics).
    assert KanbanSessionLocal is _conftest._test_sf
