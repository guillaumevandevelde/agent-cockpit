"""Bring an existing database under alembic control without rebuilding it.

Both stores predate alembic: their schema was produced by ``create_all`` plus a
set of hand-written ``_ensure_*`` / ``_migrate_*`` functions. They hold live
data (855 cards and 18.834 ops on the board at the time of writing), so the
first alembic run must stamp them as current rather than create anything.

Stamping is only safe when the real schema already matches the models. A
database whose shape drifted -- which is a live possibility given four
hand-written migration functions -- would have that drift frozen in place
permanently by a stamp, and no later revision would ever correct it. So drift
is a hard refusal, not a warning.
"""
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.schema import MetaData

from app.db_schema_drift import schema_differences

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class SchemaDriftError(RuntimeError):
    """An unversioned database's schema does not match the models."""


def _run_alembic(name: str, db_path: Path, *args: str) -> None:
    # `sys.executable -m alembic` rather than a bare `alembic`: callers may run
    # under an interpreter whose venv/bin is not on PATH (the test runner does
    # exactly that), where the console script is unresolvable.
    env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic --name {name} {' '.join(args)} failed:\n{result.stderr}")


def ensure_versioned(name: str, db_path: Path, metadata: MetaData) -> str:
    """Return "created", "stamped" or "upgraded" for the database at db_path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        tables = set(inspector.get_table_names())

    if not tables:
        _run_alembic(name, db_path, "upgrade", "head")
        return "created"

    if "alembic_version" in tables:
        _run_alembic(name, db_path, "upgrade", "head")
        return "upgraded"

    with engine.connect() as conn:
        differences = schema_differences(conn, metadata)
    if differences:
        raise SchemaDriftError(
            f"{db_path} predates alembic and its schema does not match the models, "
            "so it cannot be stamped as up to date. Differences:\n  "
            + "\n  ".join(differences)
        )

    _run_alembic(name, db_path, "stamp", "head")
    return "stamped"
