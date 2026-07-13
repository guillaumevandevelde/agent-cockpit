"""ORM model for per-project security profiles.

`ProjectSecurityProfile` is the storage backing facet D's `ProjectSecurityPolicy`
(see docs/cockpit/veilig-bouwen-en-uitleveren.md §4.3). It deliberately lives
in its own table — not in `KanbanMeta` — so portfolio-sync (facet C) can later
adopt or extend it without tangling the device-local `KanbanMeta` namespace.

The values for `risk_class` and `network_policy` are the canonical taxonomy
from docs/cockpit/risk-class-taxonomie.md. The cardinality matches the four
risk levels that policy dispatch already reasons about.

The actual *application* of this profile to spawned sessions is follow-up #5;
today callers read it and only the env-injection path uses parts of it
(see app/services/runs/spawn.py).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Conservative defaults for a freshly-created product project. Chosen so the
# "what does Cockpit do when an out-of-the-box product repo gets dispatched"
# case is safe-by-default: container transport, no permission-skipping, no
# egress, modest memory/pids. Matches kanban card
# `[security][D] ProjectSecurityPolicy-dataclass + storage`.
DEFAULT_PRODUCT_RESOURCE_QUOTA: dict[str, int] = {
    "memory_mb": 1024,
    "cpu_quota": 1,
    "pids_limit": 128,
    "disk_mb": 2048,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProjectSecurityProfile(Base):
    """One row per project_path. Looked up by path; absence triggers a default."""

    __tablename__ = "project_security_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_path: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    risk_class: Mapped[str] = mapped_column(
        String, default="product-staging", server_default="product-staging", nullable=False,
    )
    default_transport: Mapped[str] = mapped_column(
        String, default="sandcastle", server_default="sandcastle", nullable=False,
    )
    default_skip_permissions: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False,
    )
    secrets_scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_quota: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: dict(DEFAULT_PRODUCT_RESOURCE_QUOTA), nullable=False,
    )
    network_policy: Mapped[str] = mapped_column(
        String, default="allowlist", server_default="allowlist", nullable=False,
    )
    egress_allowlist: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )
