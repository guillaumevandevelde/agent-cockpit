"""Bring an existing database under alembic control without rebuilding it.

Both stores predate alembic: their schema was produced by ``create_all`` plus a
set of hand-written ``_ensure_*`` / ``_migrate_*`` functions. They hold live
data (59 cards and 18.834 ops on the board at the time of writing), so the
first alembic run must adopt them rather than create anything.

Adoption stamps the database at the *baseline* revision and then upgrades it,
so every revision written since baseline still runs. Stamping straight at head
would skip them silently -- and on both live stores that mattered: they drifted
from the models (six columns missing from ``sandcastle_configs``, five tables
outliving their features), and the reconcile revisions are what closes that.

Afterwards the schema is compared to the models and a remaining difference is a
hard error. That check is deliberately on the end state: drift must never
become invisible, and asserting it is gone after the upgrade proves exactly
that, while still letting reconcile revisions do their job.
"""
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
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


def _base_revision(name: str) -> str:
    """Revision id of the first migration in ``name``'s history."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"), ini_section=name)
    bases = ScriptDirectory.from_config(config).get_bases()
    if len(bases) != 1:
        raise RuntimeError(f"expected exactly one base revision for {name}, got {bases}")
    return bases[0]


def ensure_versioned(name: str, db_path: Path, metadata: MetaData) -> str:
    """Bring the database at ``db_path`` to head.

    Returns "created" (was empty), "upgraded" (already under alembic) or
    "adopted" (existed but predated alembic). Raises ``SchemaDriftError`` when
    the schema still differs from ``metadata`` after the upgrade.
    """
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

    # A pre-alembic database sits at the *baseline* revision's shape, not at
    # head: every revision written since baseline still has to run against it.
    # Stamping straight at head would skip them silently -- and the reconcile
    # revision is exactly such a revision, so on the live stores that would
    # have frozen the measured drift in place forever.
    _run_alembic(name, db_path, "stamp", _base_revision(name))
    _run_alembic(name, db_path, "upgrade", "head")

    # Verify the end state rather than the starting state. The point of the
    # check is that drift must never become invisible; asserting it is gone
    # after the upgrade proves that directly, and it lets reconcile revisions
    # do their job instead of being pre-empted by a refusal.
    with engine.connect() as conn:
        differences = schema_differences(conn, metadata)
    if differences:
        raise SchemaDriftError(
            f"{db_path} was adopted into alembic, but its schema still does not "
            "match the models afterwards. A reconcile revision is missing for:\n  "
            + "\n  ".join(differences)
            + "\n\nThe pre-migration snapshot taken by app/migrate_cli.py is your "
            "way back."
        )

    return "adopted"


def assert_at_head(name: str, db_path: Path) -> None:
    """Raise when the database is behind the migrations.

    Called from the app's lifespan. cockpit.sh already migrates before start,
    but a bare `uvicorn app.main:app` or a container start bypasses that, and
    serving on an unknown schema corrupts rather than errors.
    """
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        if "alembic_version" not in set(sa.inspect(conn).get_table_names()):
            raise RuntimeError(
                f"{db_path} is not under alembic control. "
                "Run: cd backend && python -m app.migrate_cli"
            )
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, "heads", "--verbose"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"},
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not read alembic heads for {name}:\n{result.stderr}")
    if current not in result.stdout:
        raise RuntimeError(
            f"{db_path} is at revision {current}, which is not head. "
            "Run: cd backend && python -m app.migrate_cli"
        )
