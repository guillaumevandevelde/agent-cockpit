import sqlalchemy as sa

from app.db_schema_drift import schema_differences


def _metadata_with_person() -> sa.MetaData:
    md = sa.MetaData()
    sa.Table(
        "person",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50)),
    )
    return md


def test_no_differences_when_schema_matches(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    md.create_all(engine)
    with engine.connect() as conn:
        assert schema_differences(conn, md) == []


def test_reports_missing_table(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["missing-table: person"]


def test_reports_missing_column(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY)"))
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["missing-column: person.name"]


def test_reports_extra_column(tmp_path):
    md = _metadata_with_person()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(50), extra TEXT)")
        )
    with engine.connect() as conn:
        assert schema_differences(conn, md) == ["extra-column: person.extra"]
