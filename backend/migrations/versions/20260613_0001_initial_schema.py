"""Create the initial Claude Cockpit schema."""
from alembic import op

from app.database import Base
import app.models.database  # noqa: F401
import app.models.scheduled_message  # noqa: F401

revision = "20260613_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
