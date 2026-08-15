"""Compare a live database's schema against SQLAlchemy model metadata.

Deliberately alembic-free: this is the gate that decides whether an existing,
unversioned database may be stamped as up to date (see ``app/db_bootstrap.py``).
Stamping a database whose real shape has drifted from the models would freeze
that drift in place permanently, so the check must not depend on the migration
machinery it guards.

Only tables and column names are compared. Types are intentionally out of
scope: SQLite's type affinity makes a faithful type comparison noisy (VARCHAR
vs TEXT, BOOLEAN vs INTEGER) without catching a real class of production bug.
"""
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.schema import MetaData


def schema_differences(sync_connection: Connection, metadata: MetaData) -> list[str]:
    """Return one human-readable line per difference; empty list means a match."""
    inspector = sa.inspect(sync_connection)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(metadata.tables)

    differences: list[str] = []
    for name in sorted(expected_tables - actual_tables):
        differences.append(f"missing-table: {name}")
    for name in sorted(actual_tables - expected_tables - {"alembic_version"}):
        differences.append(f"extra-table: {name}")

    for name in sorted(expected_tables & actual_tables):
        actual_columns = {col["name"] for col in inspector.get_columns(name)}
        expected_columns = set(metadata.tables[name].columns.keys())
        for column in sorted(expected_columns - actual_columns):
            differences.append(f"missing-column: {name}.{column}")
        for column in sorted(actual_columns - expected_columns):
            differences.append(f"extra-column: {name}.{column}")

    return differences
