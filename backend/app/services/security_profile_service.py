"""Business logic for `ProjectSecurityProfile`.

The service is the single read/write path for security profiles. The REST
layer (`app/api/v1/security.py`) delegates here so the same semantics are
re-usable from internal call-sites (Blueprint apply, repo bootstrap, etc).

Audit logging writes to the dedicated ``security_audit`` table on every
real risk-class transition. The ``record`` helper is best-effort so a
broken audit insert never aborts the profile write itself.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.security_audit import SecurityAuditKind
from app.models.security_profile import (
    DEFAULT_PRODUCT_RESOURCE_QUOTA,
    ProjectSecurityProfile,
)
from app.services.security_audit_service import record as audit_record

logger = logging.getLogger(__name__)


def _default_for_product() -> dict[str, Any]:
    """The conservative default profile for a new product project.

    Hardcoded rather than reading the model's server_default so callers and
    tests see the same shape regardless of DB state — and so a future tweak
    to the ORM default doesn't silently drift the API contract.
    """
    return {
        "risk_class": "product-staging",
        "default_transport": "sandcastle",
        "default_skip_permissions": False,
        "secrets_scope_id": None,
        "resource_quota": dict(DEFAULT_PRODUCT_RESOURCE_QUOTA),
        "network_policy": "allowlist",
        "egress_allowlist": [],
    }


async def _audit_risk_class_transition(
    db: AsyncSession,
    project_path: str,
    before: str,
    after: str,
) -> None:
    """Persist a ``security_profile_change`` row for the transition.

    Best-effort: a failed insert logs and continues. The profile write
    above is the security-relevant action; this is observability.

    Also keeps emitting the human-readable ``logger.info`` line that the
    pre-table world relied on (see ``test_risk_class_transition_logs_audit_event``).
    Both surfaces describe the same event — the table for queries,
    the logger for grep — and a future migration that drops the logger
    line can do so cleanly.
    """
    logger.info(
        "security.risk_class_transition project_path=%s before=%s after=%s",
        project_path,
        before,
        after,
    )
    await audit_record(
        db,
        kind=SecurityAuditKind.SECURITY_PROFILE_CHANGE,
        project_key=project_path,
        actor="security-profile-api",
        payload_ref={"field": "risk_class", "before": before, "after": after},
    )


class SecurityProfileService:
    """Read/write lifecycle for security profiles.

    `project_path` is the natural key. There is at most one row per path;
    `get_or_create_for_project` is the canonical lazy-create entry point —
    the REST GET delegates here so the first hit against a brand-new project
    persists the safe-by-default row, and subsequent PATCHes work without a
    404 dance.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------- read

    async def get(self, project_path: str) -> ProjectSecurityProfile | None:
        """Return the persisted row, or None when the project has no profile yet."""
        result = await self.db.execute(
            select(ProjectSecurityProfile).where(
                ProjectSecurityProfile.project_path == project_path
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_project(
        self,
        project_path: str,
        project_kind: str = "product",
    ) -> ProjectSecurityProfile:
        """Return an existing row, or insert a default one for a new project.

        ``project_kind`` chooses the default family. Today only ``product``
        has a calibrated default; ``meta`` falls back to the product default
        too (the taxonomy doc specifies meta risk_class=meta, but the
        follow-up that wires the classifier is #12 — until then the safe-
        by-default profile is the conservative fallback rather than risking
        a permissive gap).

        First GET against a new project lazily materialises the safe-by-
        default row so subsequent PATCHes don't fail with 404. Subsequent
        GETs return the persisted row.
        """
        row = await self.get(project_path)
        if row is not None:
            return row
        defaults = _default_for_product()
        new_row = ProjectSecurityProfile(
            project_path=project_path,
            risk_class=defaults["risk_class"],
            default_transport=defaults["default_transport"],
            default_skip_permissions=defaults["default_skip_permissions"],
            secrets_scope_id=defaults["secrets_scope_id"],
            resource_quota=dict(defaults["resource_quota"]),
            network_policy=defaults["network_policy"],
            egress_allowlist=list(defaults["egress_allowlist"]),
        )
        self.db.add(new_row)
        await self.db.commit()
        await self.db.refresh(new_row)
        return new_row

    # ------------------------------------------------------------------- write

    async def upsert(self, project_path: str, payload: dict[str, Any]) -> ProjectSecurityProfile:
        """PUT semantics: replace every writable field with the payload."""
        row = await self.get(project_path)
        if row is None:
            row = ProjectSecurityProfile(project_path=project_path)
            self.db.add(row)

        before_risk = row.risk_class
        for field in (
            "risk_class",
            "default_transport",
            "default_skip_permissions",
            "secrets_scope_id",
            "resource_quota",
            "network_policy",
            "egress_allowlist",
        ):
            if field in payload:
                value = payload[field]
                if field in ("resource_quota", "egress_allowlist") and value is not None:
                    value = dict(value) if isinstance(value, dict) else list(value)
                setattr(row, field, value)

        await self.db.commit()
        await self.db.refresh(row)

        # Audit only real transitions. PUT against a brand-new project materialises
        # the row at the ORM-default risk_class first, so we treat `before is None`
        # as a creation, not a transition — even though the final value matches
        # the default, the user is explicitly setting it for the first time.
        if "risk_class" in payload and before_risk is not None and before_risk != row.risk_class:
            await _audit_risk_class_transition(self.db, project_path, before_risk, row.risk_class)

        return row

    async def patch(self, project_path: str, payload: dict[str, Any]) -> ProjectSecurityProfile | None:
        """PATCH semantics: only fields present in the payload are applied."""
        row = await self.get(project_path)
        if row is None:
            return None

        before_risk = row.risk_class
        if "resource_quota" in payload and payload["resource_quota"] is not None:
            row.resource_quota = dict(payload["resource_quota"])
        if "egress_allowlist" in payload and payload["egress_allowlist"] is not None:
            row.egress_allowlist = list(payload["egress_allowlist"])

        for field in (
            "risk_class",
            "default_transport",
            "default_skip_permissions",
            "secrets_scope_id",
            "network_policy",
        ):
            if field in payload:
                setattr(row, field, payload[field])

        await self.db.commit()
        await self.db.refresh(row)

        if "risk_class" in payload and before_risk != row.risk_class:
            await _audit_risk_class_transition(self.db, project_path, before_risk, row.risk_class)

        return row

    async def delete(self, project_path: str) -> bool:
        """Remove the row. Returns ``True`` iff a row was actually deleted."""
        row = await self.get(project_path)
        if row is None:
            return False
        await self.db.delete(row)
        await self.db.commit()
        return True