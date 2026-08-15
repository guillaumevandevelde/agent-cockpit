"""reconcile pre alembic drift

Revision ID: ad655295ed60
Revises: c18135689409
Create Date: 2026-08-15

Brings a board database that predates alembic into line with the models.

Two leftovers were measured on the live board on 2026-08-15:

1. ``kanban_plans`` -- the plan table of a feature phased out by decision (see
   ``app/api/v1/plans.py``). No model declares it and it held zero rows.
2. ``kanban_columns.default_platform`` -- the old name of what is now
   ``default_provider``. The rename branch in ``app/kanban/db.py`` only fires
   when the new column is absent, so on a database that already had
   ``default_provider`` the old column was never dropped. It held zero
   non-null values.

Both steps are conditional on what the database actually contains, because
this revision also runs on a fresh database built by the baseline revision,
where neither leftover exists.

SQLite cannot drop a column in place, so the column work goes through
batch_alter_table, which rebuilds the table.
"""
import sqlalchemy as sa
from alembic import op

revision = 'ad655295ed60'
down_revision = 'c18135689409'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "kanban_plans" in tables:
        op.drop_table("kanban_plans")

    if "kanban_columns" in tables:
        columns = {col["name"] for col in inspector.get_columns("kanban_columns")}
        if "default_platform" in columns:
            with op.batch_alter_table("kanban_columns") as batch_op:
                batch_op.drop_column("default_platform")


def downgrade() -> None:
    # Deliberately not reversible. kanban_plans belongs to a retired feature
    # with no model left to recreate it from, and default_platform is the dead
    # half of a completed rename. Restoring the pre-upgrade shape means
    # restoring the snapshot that app/migrate_cli.py takes first.
    pass
