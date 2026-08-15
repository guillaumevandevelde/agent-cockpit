"""reconcile pre alembic drift

Revision ID: e2fa1bd8f45d
Revises: a31f18593f46
Create Date: 2026-08-15

Brings a registry database that predates alembic into line with the models.

Two classes of drift were measured on the live store on 2026-08-15:

1. ``sandcastle_configs`` was missing six columns the model declares. That was
   not cosmetic: ``SandcastleConfig`` is queried by ``sandcastle_service`` and
   every such select failed with ``no such column:
   sandcastle_configs.memory_limit_mb``. ``create_all`` only ever creates
   missing *tables*, so a column added to an existing model never reached the
   database and no hand-written migration covered it.
2. Four tables outlived the features that owned them. None has a model left
   anywhere in ``app/``, nothing references them by foreign key, and together
   they held one row -- the never-delivered message from the retired
   scheduled-messages feature.

Every step is conditional on what the database actually contains, because this
revision also runs on a fresh database built by the baseline revision, where
the columns already exist and the vestigial tables never did.

SQLite cannot ALTER a column, so column work goes through batch_alter_table.
"""
import sqlalchemy as sa
from alembic import op

revision = 'e2fa1bd8f45d'
down_revision = 'a31f18593f46'
branch_labels = None
depends_on = None

_MISSING_COLUMNS = [
    sa.Column("memory_limit_mb", sa.Integer(), nullable=True),
    sa.Column("cpu_quota", sa.Float(), nullable=True),
    sa.Column("pids_limit", sa.Integer(), nullable=True),
    sa.Column("read_only_rootfs", sa.Boolean(), nullable=True),
    sa.Column("network_mode", sa.String(16), nullable=True),
    sa.Column("egress_allowlist", sa.JSON(), nullable=True),
]

_VESTIGIAL_TABLES = [
    "delivery_attempts",
    "scheduled_messages",
    "agent_team_members",
    "agent_teams",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "sandcastle_configs" in tables:
        existing = {col["name"] for col in inspector.get_columns("sandcastle_configs")}
        missing = [col for col in _MISSING_COLUMNS if col.name not in existing]
        if missing:
            with op.batch_alter_table("sandcastle_configs") as batch_op:
                for column in missing:
                    batch_op.add_column(column)

    # Ordered so a child table goes before the parent it points at.
    for table in _VESTIGIAL_TABLES:
        if table in tables:
            op.drop_table(table)


def downgrade() -> None:
    # Deliberately not reversible. The four dropped tables belong to features
    # that were retired by decision, and their definitions no longer exist in
    # the codebase to recreate from. Restoring this database to its pre-upgrade
    # shape means restoring the snapshot that app/migrate_cli.py takes first.
    pass
