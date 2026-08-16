"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

SQLite cannot ALTER a column. Any change to an existing column MUST go through
`with op.batch_alter_table("<table>") as batch_op:` — a plain op.alter_column
will fail at runtime on both of this project's stores.

`init_db` roept nog steeds `Base.metadata.create_all` aan, dus een tabel kan al
bestaan voordat de revisie draait (de app een keer starten volstaat). Maak een
`op.create_table` daarom voorwaardelijk:

    if "<tabel>" in sa.inspect(op.get_bind()).get_table_names():
        return
"""
import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
