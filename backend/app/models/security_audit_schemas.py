"""Pydantic schemas for the read-only security-audit REST endpoint.

The table itself has no write schema: rows are inserted by the
invulpunten (services that own each security-relevant action), never
by a request body. This file is therefore just the response shape +
a thin filter-input model for the GET endpoint.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.security_audit import SecurityAuditKind


def _serialize_naive_as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; tag the value as UTC on the way out.

    Mirrors ``_as_utc_iso`` in ``scheduled_message_schemas.py`` — same
    DB-engine quirk, same fix.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class SecurityAuditEntry(BaseModel):
    """One row in the audit log, as returned by the REST endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: SecurityAuditKind
    project_key: str
    actor: str
    payload_ref: dict[str, Any] = Field(default_factory=dict)
    at: datetime

    @field_serializer("at")
    def _ser_at(self, dt: datetime) -> datetime:
        return _serialize_naive_as_utc(dt)


class SecurityAuditListResponse(BaseModel):
    """Response envelope for ``GET /api/v1/security/audit``.

    ``entries`` is newest-first. ``total`` is the count *after* filters
    but *before* the limit is applied, so a UI can show "showing N of M"
    even when it caps the page size.
    """

    entries: list[SecurityAuditEntry]
    total: int
    limit: int