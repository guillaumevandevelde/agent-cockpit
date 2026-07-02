"""Tunable magic numbers must live in Settings, not be hardcoded at call sites."""
from app.config import Settings


def test_kanban_dispatch_interval_default():
    assert Settings().kanban_dispatch_interval_seconds == 10


def test_provider_doctor_timeout_default():
    assert Settings().provider_doctor_timeout_seconds == 30


def test_sqlite_busy_timeout_default():
    assert Settings().sqlite_busy_timeout_ms == 5000


def test_default_backup_retention_days_default():
    assert Settings().default_backup_retention_days == 7


def test_tuning_settings_are_overridable(monkeypatch):
    monkeypatch.setenv("KANBAN_DISPATCH_INTERVAL_SECONDS", "25")
    monkeypatch.setenv("PROVIDER_DOCTOR_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "8000")
    monkeypatch.setenv("DEFAULT_BACKUP_RETENTION_DAYS", "14")
    s = Settings()
    assert s.kanban_dispatch_interval_seconds == 25
    assert s.provider_doctor_timeout_seconds == 45
    assert s.sqlite_busy_timeout_ms == 8000
    assert s.default_backup_retention_days == 14
