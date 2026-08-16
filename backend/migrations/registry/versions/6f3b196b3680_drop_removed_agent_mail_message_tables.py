"""drop removed agent-mail message tables

Revision ID: 6f3b196b3680
Revises: e2fa1bd8f45d
Create Date: 2026-08-15

De berichten-, mailbox- en receipt-laag van Agent Mail is verwijderd in commit
97e821fc (kaart 46930d26). De modellen verdwenen, de tabellen bleven -- er was
nog geen migratie toen die refactor landde. Deze revisie maakt het schema
gelijk aan de modellen.

Alle drie de tabellen waren leeg op de live registry-database toen dit werd
geschreven, dus er gaat geen gegeven verloren. De roster-laag (MailTeamMember)
blijft ongemoeid; die hield zijn gewicht wel.

SQLite kan geen kolom wijzigen, vandaar batch_alter_table.
"""
import sqlalchemy as sa
from alembic import op

revision = '6f3b196b3680'
down_revision = 'e2fa1bd8f45d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Geen batch_alter_table met drop_index hier. Op SQLite kopieert batch-modus
    # de tabel en draagt de indexen niet mee, waardoor een drop_index erbinnen
    # faalt met "no such index" op een verse database. `op.drop_table` ruimt de
    # indexen van een tabel sowieso mee op.
    #
    # Voorwaardelijk omdat `init_db` nog `create_all` draait en een database dus
    # in beide vormen kan staan.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("mail_receipts", "mail_messages", "mail_external_actors"):
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    # Bewust niet omkeerbaar: de modellen bestaan niet meer, dus er is niets om
    # de tabellen uit te herbouwen. Terug wil je via de momentopname die
    # app/migrate_cli.py vooraf maakt.
    pass
