"""Fail when the migrations no longer describe the models.

The test suite builds its schema with drop_all/create_all (37 test files depend
on that, and migrating per test would only make the suite slower), so nothing
else notices when a model changes without a matching revision. This gate closes
that hole: it takes a fresh database to head and compares the result against the
model metadata.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.db_schema_drift import schema_differences  # noqa: E402

TARGETS = [
    ("registry", ["app.models", "app.models.database"], "app.database", "Base"),
    ("kanban", ["app.kanban.models"], "app.kanban.db", "KanbanBase"),
]


def _metadata(model_modules: list[str], base_module: str, base_attr: str):
    # Importing the base is not enough — a table only lands on the metadata when
    # its own module is imported.
    for module in model_modules:
        __import__(module)
    base = __import__(base_module, fromlist=[base_attr])
    return getattr(base, base_attr).metadata


def main() -> int:
    failures = 0
    for name, model_modules, base_module, base_attr in TARGETS:
        metadata = _metadata(model_modules, base_module, base_attr)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / f"{name}.db"
            env = {**os.environ, "ALEMBIC_DATABASE_URL": f"sqlite:///{db_path}"}
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "--name", name, "upgrade", "head"],
                cwd=BACKEND_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"{name}: alembic upgrade head failed\n{result.stderr}", file=sys.stderr)
                failures += 1
                continue

            engine = sa.create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                differences = schema_differences(conn, metadata)
            engine.dispose()

            if differences:
                print(f"{name}: models and migrations disagree:", file=sys.stderr)
                for line in differences:
                    print(f"  {line}", file=sys.stderr)
                print(
                    "\nGenerate the missing revision:\n"
                    f"  cd backend && python -m alembic --name {name} revision "
                    "--autogenerate -m '<what changed>'",
                    file=sys.stderr,
                )
                failures += 1

    if failures:
        return 1
    print("OK: migrations match the models for both stores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
