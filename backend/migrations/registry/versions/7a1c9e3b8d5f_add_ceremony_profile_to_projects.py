"""add ceremony_profile to projects

Revision ID: 7a1c9e3b8d5f
Revises: b015af7f5f1b
Create Date: 2026-08-16

Adds the per-project ``ceremony_profile`` column that distinguishes
``code`` projects (the default — full PR/ship workflow) from
``knowledge`` projects (lighter profile, no tests, no PR, deliverable
is a note or document). Decision: ``cockpit-richting-decision.md`` §4.
The dispatch hot path reads this value to pick the matching
session-end recipe (see ``backend/app/kanban/dispatch.py``).

Default is ``code`` so every existing project keeps its current
behaviour; only projects that explicitly opt in via
``PATCH /api/v1/projects/{id}`` switch to the lighter profile.

SQLite cannot drop a column in place, but ADD COLUMN with a
``DEFAULT`` clause is supported — so this migration is a single
``add_column`` and no batch rebuild is required.
"""
import sqlalchemy as sa
from alembic import op

revision = '7a1c9e3b8d5f'
down_revision = 'b015af7f5f1b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {
        col["name"] for col in inspector.get_columns("projects")
    } if "projects" in inspector.get_table_names() else set()
    if "ceremony_profile" in existing_cols:
        # No-op when the column already exists: ``Base.metadata.create_all``
        # (called from ``init_db`` on every boot) would have created it on a
        # fresh database once the model gains the field, so a database that
        # has been booted at least once between model change and migration
        # land already carries the column. Mirrors the conditional guard in
        # ``6f3b196b3680_drop_removed_agent_mail_message_tables.py``.
        return
    op.add_column(
        "projects",
        sa.Column(
            "ceremony_profile",
            sa.String(length=16),
            nullable=False,
            server_default="code",
        ),
    )


def downgrade() -> None:
    # SQLite has no DROP COLUMN pre-3.35 without batch_recreate; this codebase
    # bundles a 3.45+ SQLite via pysqlite-binary, so the plain drop works.
    # Reversibility is intentionally limited — rolling back a ceremony_profile
    # change is a no-op for projects that never opted in, and a manual
    # cleanup for the ones that did. ``scripts/check_migrations_current.py``
    # verifies the up direction; downgrade is a developer convenience only.
    op.drop_column("projects", "ceremony_profile")