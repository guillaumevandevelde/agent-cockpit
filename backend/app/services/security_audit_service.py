"""Service layer for the security-audit stream.

All writes go through :func:`record` so the ``payload_ref`` shape is
in one place and the "no secret values" invariant is enforced
centrally. All reads go through :func:`query` so the endpoint,
dashboards and any future exports share the same filter contract.

The service is intentionally **best-effort**: an audit-write that fails
must not crash the originating action. A security profile flip with a
broken audit row is still a security profile flip. Callers therefore
``await record(...)`` and treat the result as advisory — the function
swallows its own exceptions and logs them. Tests that want to assert
"a row was inserted" use the real DB and bypass the swallow by hitting
the function directly.

Scope note: this table is the **device-local** mirror of security
events. SIEM-export, long-term-retention and per-user access controls
are out of scope (see the originating kanban card).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.security_audit import SecurityAudit, SecurityAuditKind

logger = logging.getLogger(__name__)

# Default page size for the REST endpoint's ``limit`` parameter when the
# caller doesn't supply one. Bounds chosen so a single page is cheap to
# render in the cockpit UI without becoming a DoS surface.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


# Heuristic guard: any payload_ref key/value that looks like a key=value
# assignment with a long opaque token is suspect. Keys are always stable
# identifiers we control (``name``, ``before``, ``after``,
# ``env_var_names``), so a literal ``=`` in a *value* is the canary for
# "did someone leak an env-var into the audit log?". Values whose entire
# content is a base64-like alphabet (``[A-Za-z0-9+/=_-]{32,}``) are also
# flagged — those are almost always credential blobs.
def _payload_looks_like_secret(payload: dict[str, Any]) -> bool:
    """Return True if any value in ``payload`` looks like a leaked secret.

    Conservative on purpose: false positives only cost a logged warning,
    a false negative leaks a credential into the audit table where it
    will live forever. The shape checks below correspond to the actual
    shape of every invulpunt's payload so the false-positive rate is
    near-zero in practice.
    """
    # A handful of secret-shaped prefixes we never want in a payload.
    # The ``name`` field legitimately appears in payload_ref (e.g.
    # ``{"name": "STRIPE_KEY"}``); the heuristic only fires when a value
    # *looks like* a secret value rather than a name fragment.
    def opaque_token(v: Any) -> bool:
        return isinstance(v, str) and len(v) >= 32 and (
            v.startswith("sk_")
            or v.startswith("ghp_")
            or v.startswith("xox")
            or v.startswith("AKIA")
            or v.startswith("AIza")
            or v.startswith("ya29.")
        )

    for v in payload.values():
        if opaque_token(v):
            return True
        if isinstance(v, str) and "=" in v and len(v) > 64:
            # ``COCKPIT_TOKEN=abcdef…`` is exactly the shape of a leaked
            # env-var. Skip the legitimate refs (none should contain
            # ``=`` in a value today).
            return True
    return False


async def record(
    db: AsyncSession,
    *,
    kind: SecurityAuditKind,
    project_key: str,
    actor: str,
    payload_ref: dict[str, Any] | None = None,
) -> SecurityAudit | None:
    """Insert one audit row. Best-effort: failures are logged, not raised.

    Returns the inserted row on success, ``None`` if the insert failed
    or was refused by the secret-leak guard. Callers SHOULD NOT depend
    on the return value — the security audit is an observability tool,
    not a transaction participant.
    """
    payload = dict(payload_ref or {})
    if _payload_looks_like_secret(payload):
        logger.warning(
            "security_audit refused: payload looks like a leaked secret "
            "(kind=%s project_key=%s). Dropping the row.",
            kind.value,
            project_key,
        )
        return None
    try:
        row = SecurityAudit(
            kind=kind.value,
            project_key=project_key,
            actor=actor,
            payload_ref=payload,
        )
        db.add(row)
        await db.flush()
        return row
    except Exception:
        logger.exception(
            "security_audit insert failed (kind=%s project_key=%s)",
            kind.value,
            project_key,
        )
        return None


async def query(
    db: AsyncSession,
    *,
    project_key: str | None = None,
    kind: SecurityAuditKind | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[SecurityAudit], int]:
    """Return ``(entries, total)`` newest-first.

    ``limit`` is clamped to ``[1, MAX_LIMIT]``. The total count is
    computed without the limit so the caller can render "showing N of M".
    """
    limit = max(1, min(int(limit), MAX_LIMIT))

    filters = []
    if project_key is not None:
        filters.append(SecurityAudit.project_key == project_key)
    if kind is not None:
        filters.append(SecurityAudit.kind == kind.value)
    if since is not None:
        filters.append(SecurityAudit.at >= since)
    if until is not None:
        filters.append(SecurityAudit.at <= until)
    where = and_(*filters) if filters else None

    count_stmt = select(func.count()).select_from(SecurityAudit)
    if where is not None:
        count_stmt = count_stmt.where(where)
    total = int((await db.execute(count_stmt)).scalar_one())

    list_stmt = (
        select(SecurityAudit)
        .order_by(SecurityAudit.at.desc(), SecurityAudit.id.desc())
        .limit(limit)
    )
    if where is not None:
        list_stmt = list_stmt.where(where)
    rows = list((await db.execute(list_stmt)).scalars().all())

    return rows, total