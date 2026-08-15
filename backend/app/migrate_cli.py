"""Bring both stores to head, taking a snapshot of the board first.

Run by scripts/cockpit.sh before the backend starts. Deliberately loud: a
failed migration stops the start rather than letting the backend come up on a
schema it does not understand.
"""
import sys
from datetime import datetime
from pathlib import Path

# Importing the bases is not enough: a table only lands on a DeclarativeBase's
# metadata when its module is imported. Without these three the metadata is
# empty and the post-upgrade drift check reports every existing table as
# "extra" -- which is exactly what happened on the first live run.
import app.kanban.models  # noqa: F401
import app.models  # noqa: F401
import app.models.database  # noqa: F401
from app.config import settings
from app.database import Base
from app.db_bootstrap import SchemaDriftError, ensure_versioned, sqlite_path
from app.kanban.db import KanbanBase
from app.services.backup_service import kanban_db_path
from app.sqlite_snapshot import snapshot_sqlite_db


def main() -> int:
    board = kanban_db_path()
    if board is not None and board.exists():
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        destination = Path.home() / ".claude-registry" / "backups" / f"pre-migrate-{stamp}.db"
        snapshot_sqlite_db(board, destination)
        print(f"snapshot: {destination}")

    registry = sqlite_path(settings.database_url)

    try:
        if registry is not None:
            print("registry:", ensure_versioned("registry", registry, Base.metadata))
        else:
            print("registry: skipped (not a file-backed sqlite store)")
        if board is not None:
            print("kanban:", ensure_versioned("kanban", board, KanbanBase.metadata))
        else:
            print("kanban: skipped (not a file-backed sqlite store)")
    except SchemaDriftError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
