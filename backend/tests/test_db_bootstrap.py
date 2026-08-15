"""Adoption of pre-alembic databases.

These tests run the real migration histories against tmp_path files. They never
touch ~/.claude-registry/kanban.db or backend/claude_registry.db.
"""
import pytest
import sqlalchemy as sa

import app.models  # noqa: F401  (register every table on Base)
import app.models.database  # noqa: F401
from app.database import Base
from app.db_bootstrap import (
    SchemaDriftError,
    _base_revision,
    _run_alembic,
    assert_at_head,
    ensure_versioned,
    lifespan_schema_check,
)
from app.db_schema_drift import schema_differences


def _drop_version_table(db) -> None:
    """Make a migrated database look like one that predates alembic."""
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE alembic_version"))


def test_empty_database_is_created(tmp_path):
    db = tmp_path / "empty.db"
    assert ensure_versioned("registry", db, Base.metadata) == "created"


def test_already_versioned_database_is_upgraded(tmp_path):
    db = tmp_path / "versioned.db"
    ensure_versioned("registry", db, Base.metadata)
    assert ensure_versioned("registry", db, Base.metadata) == "upgraded"


def test_pre_alembic_database_is_adopted(tmp_path):
    db = tmp_path / "pre.db"
    ensure_versioned("registry", db, Base.metadata)
    _drop_version_table(db)

    assert ensure_versioned("registry", db, Base.metadata) == "adopted"

    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        assert schema_differences(conn, Base.metadata) == []


def test_adoption_preserves_existing_rows(tmp_path):
    db = tmp_path / "filled.db"
    ensure_versioned("registry", db, Base.metadata)
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        # Every NOT NULL column is spelled out rather than leaning on the
        # model's Python-side defaults, so the row is inserted by the same
        # plain SQL path the migration itself uses.
        conn.execute(
            sa.text(
                "INSERT INTO projects "
                "(name, path, is_active, kind, last_accessed, created_at, updated_at) "
                "VALUES ('keep-me', '/tmp/x', 1, 'app', "
                "'2026-01-01', '2026-01-01', '2026-01-01')"
            )
        )
    _drop_version_table(db)

    ensure_versioned("registry", db, Base.metadata)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT name FROM projects")).scalar() == "keep-me"


def test_adoption_reports_drift_no_revision_reconciles(tmp_path):
    db = tmp_path / "drift.db"
    ensure_versioned("registry", db, Base.metadata)
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE leftover_from_nowhere (id INTEGER)"))
    _drop_version_table(db)

    with pytest.raises(SchemaDriftError, match="leftover_from_nowhere"):
        ensure_versioned("registry", db, Base.metadata)


def test_assert_at_head_refuses_unversioned_database(tmp_path):
    db = tmp_path / "bare.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))

    with pytest.raises(RuntimeError, match="not under alembic control"):
        assert_at_head("registry", db)


def test_assert_at_head_accepts_a_migrated_database(tmp_path):
    db = tmp_path / "current.db"
    ensure_versioned("registry", db, Base.metadata)

    assert_at_head("registry", db)  # must not raise


def test_lifespan_check_skips_a_create_all_database(tmp_path):
    """The shape the test suite itself runs on must not fail the check."""
    db = tmp_path / "create_all.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()

    assert lifespan_schema_check("registry", db) == "skipped"


def test_lifespan_check_skips_a_missing_database(tmp_path):
    assert lifespan_schema_check("registry", tmp_path / "not-there.db") == "skipped"


def test_lifespan_check_accepts_a_migrated_database(tmp_path):
    db = tmp_path / "current.db"
    ensure_versioned("registry", db, Base.metadata)

    assert lifespan_schema_check("registry", db) == "current"


def test_lifespan_check_refuses_a_versioned_database_that_is_behind(tmp_path):
    """Code moved forward, database did not -- the case this guard exists for."""
    db = tmp_path / "behind.db"
    ensure_versioned("registry", db, Base.metadata)
    # Rewind the recorded revision to the baseline without touching the schema.
    _run_alembic("registry", db, "stamp", _base_revision("registry"))

    with pytest.raises(RuntimeError, match="not head"):
        lifespan_schema_check("registry", db)
