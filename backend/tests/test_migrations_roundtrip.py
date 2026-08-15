import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db_schema_drift import schema_differences

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic(name: str, db_path: Path, *args: str) -> subprocess.CompletedProcess:
    # `sys.executable -m alembic` rather than a bare `alembic`: the test runner
    # invokes the venv interpreter directly without putting venv/bin on PATH,
    # so the console script is not resolvable. Going through the interpreter
    # also guarantees the subprocess uses the same environment as the test.
    env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "--name", name, *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "name, metadata_path",
    [
        ("registry", "app.database:Base"),
        ("kanban", "app.kanban.db:KanbanBase"),
    ],
)
def test_fresh_upgrade_matches_model_metadata(tmp_path, name, metadata_path):
    """A fresh DB taken to head must equal what the models describe."""
    module_path, attr = metadata_path.split(":")
    module = __import__(module_path, fromlist=[attr])
    metadata = getattr(module, attr).metadata

    db_path = tmp_path / f"{name}.db"
    result = _alembic(name, db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert schema_differences(conn, metadata) == []
