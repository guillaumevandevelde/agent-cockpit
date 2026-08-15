import sqlalchemy as sa

from app.sqlite_snapshot import snapshot_sqlite_db


def test_snapshot_copies_committed_rows(tmp_path):
    src = tmp_path / "live.db"
    engine = sa.create_engine(f"sqlite:///{src}")
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA journal_mode=WAL"))
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))
        conn.execute(sa.text("INSERT INTO t (v) VALUES ('kept')"))

    dest = snapshot_sqlite_db(src, tmp_path / "snap.db")

    snap_engine = sa.create_engine(f"sqlite:///{dest}")
    with snap_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT v FROM t")).scalar() == "kept"


def test_snapshot_is_a_separate_file(tmp_path):
    src = tmp_path / "live.db"
    engine = sa.create_engine(f"sqlite:///{src}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (v TEXT)"))

    dest = snapshot_sqlite_db(src, tmp_path / "snap.db")
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO t (v) VALUES ('after')"))

    snap_engine = sa.create_engine(f"sqlite:///{dest}")
    with snap_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM t")).scalar() == 0
