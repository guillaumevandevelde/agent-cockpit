import pytest
import sqlalchemy as sa

from app.db_bootstrap import SchemaDriftError, ensure_versioned


def _metadata() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "person",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50)),
    )
    return md


def test_empty_database_is_created(tmp_path):
    db = tmp_path / "empty.db"
    assert ensure_versioned("registry", db, _metadata()) == "created"


def test_matching_unversioned_database_is_stamped(tmp_path):
    db = tmp_path / "existing.db"
    md = _metadata()
    engine = sa.create_engine(f"sqlite:///{db}")
    md.create_all(engine)

    assert ensure_versioned("registry", db, md) == "stamped"

    with engine.connect() as conn:
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_stamping_preserves_existing_rows(tmp_path):
    db = tmp_path / "filled.db"
    md = _metadata()
    engine = sa.create_engine(f"sqlite:///{db}")
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO person (id, name) VALUES (1, 'kept')"))

    ensure_versioned("registry", db, md)

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT name FROM person")).scalar() == "kept"


def test_drifted_unversioned_database_is_refused(tmp_path):
    db = tmp_path / "drifted.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY)"))

    with pytest.raises(SchemaDriftError) as excinfo:
        ensure_versioned("registry", db, _metadata())

    assert "missing-column: person.name" in str(excinfo.value)


def test_assert_at_head_refuses_unversioned_database(tmp_path):
    from app.db_bootstrap import assert_at_head

    db = tmp_path / "bare.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))

    with pytest.raises(RuntimeError, match="not under alembic control"):
        assert_at_head("registry", db)


def test_assert_at_head_accepts_a_stamped_database(tmp_path):
    db = tmp_path / "stamped.db"
    md = _metadata()
    engine = sa.create_engine(f"sqlite:///{db}")
    md.create_all(engine)
    ensure_versioned("registry", db, md)

    from app.db_bootstrap import assert_at_head

    assert_at_head("registry", db)  # must not raise
