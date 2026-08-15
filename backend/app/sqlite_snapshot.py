"""WAL-safe snapshot of a SQLite database file.

Copying the file directly is wrong for a WAL-mode database: the committed
frame may live in the -wal sidecar and a plain copy misses it. sqlite3's own
backup API walks the connection instead, which sees the committed state.

Extracted from BackupService._snapshot_kanban_db so the migration runner can
take a pre-upgrade snapshot without constructing a BackupService (which needs
an AsyncSession). That method now delegates here.
"""
import sqlite3
from pathlib import Path


def snapshot_sqlite_db(src: Path, dest: Path) -> Path:
    """Copy the committed state of ``src`` to ``dest``; returns ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Default mode, NOT mode=ro. A read-only connection cannot always see the
    # WAL frames -- it needs the -shm sidecar and may not be able to create it
    # -- so a read-only snapshot can silently be hours behind the live board.
    # This constraint is inherited from BackupService._snapshot_kanban_db,
    # which documented it against the kanban engine's WAL mode. The backup API
    # only ever reads from this connection.
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest
