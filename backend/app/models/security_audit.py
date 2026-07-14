"""ORM model for the security-audit stream.

A read-only log of security-relevant events — policy flips, secret-store
mutations, env-injection (names only), run lifecycle, profile changes.
Distinct from the kanban activity-feed because security-events need their
own retention and access policy (see
``docs/cockpit/veilig-bouwen-en-uitleveren.md`` §4.8 + §5.2 "Apart").

The table has *no* write API. Every insert goes through one of the
invulpunten listed on the originating kanban card
(``[security][D] Security-audit-log + endpoint``); a future change that
wants to bypass the invulpunten and write rows directly will fail review.

The ``payload_ref`` column stores **references only** — secret names,
project keys, instance ids, resource-quota deltas. **Secret values are
forbidden** here; the ``record`` helper at the service layer rejects any
payload_ref whose keys look like they might carry a value, and the
tests pin that contract.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SecurityAuditKind(StrEnum):
    """The closed set of security-event kinds we record.

    A string-valued enum so the database stores the literal kind rather
    than an int (easier to grep, easier to filter in the REST endpoint).
    The REST endpoint validates that callers' ``?kind=…`` filter is one
    of these values.
    """

    SKIP_PERMISSIONS_FLIP = "skip_permissions_flip"
    TRANSPORT_CHANGE = "transport_change"
    AUTODISPATCH_CHANGE = "autodispatch_change"
    SECRETS_PUT = "secrets_put"
    SECRETS_DELETE = "secrets_delete"
    ENV_INJECT = "env_inject"
    SANDCASTLE_CONFIG_CHANGE = "sandcastle_config_change"
    RUN_START = "run_start"
    RUN_STOP = "run_stop"
    SECURITY_PROFILE_CHANGE = "security_profile_change"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SecurityAudit(Base):
    """One row per security-relevant event.

    ``payload_ref`` is a free-form JSON document whose shape depends on
    ``kind`` (e.g. ``{"name": "STRIPE_KEY"}`` for ``secrets_put``,
    ``{"before": "worktree", "after": "sandcastle"}`` for
    ``transport_change``). See ``backend/app/services/security_audit_service.py``
    for the per-kind contract.
    """

    __tablename__ = "security_audit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )

    __table_args__ = (
        # The common dashboard query is "what happened to project X in
        # the last hour, newest first". A composite (project_key, at)
        # index serves that without scanning the whole table.
        Index("ix_security_audit_project_at", "project_key", "at"),
    )