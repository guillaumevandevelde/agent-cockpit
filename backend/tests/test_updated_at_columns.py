"""Verify every ORM model in database.py has an updated_at column with onupdate."""
import pytest

from app.models.database import (
    AutoBackupSettings,
    Backup,
    MCPServerCache,
    Marketplace,
    PresenceEvent,
    PresenceSession,
    Project,
    SessionCache,
    UsageCache,
)

MODELS = [
    AutoBackupSettings,
    Backup,
    MCPServerCache,
    Marketplace,
    PresenceEvent,
    PresenceSession,
    Project,
    SessionCache,
    UsageCache,
]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_model_has_updated_at_column(model):
    col_names = {c.key for c in model.__table__.columns}
    assert "updated_at" in col_names


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_updated_at_has_onupdate(model):
    col = model.__table__.columns["updated_at"]
    assert col.onupdate is not None, f"{model.__name__}.updated_at has no onupdate"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_updated_at_has_default(model):
    col = model.__table__.columns["updated_at"]
    assert col.default is not None, f"{model.__name__}.updated_at has no default"
