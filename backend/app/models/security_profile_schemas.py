"""Pydantic schemas for the `ProjectSecurityProfile` REST contract.

Mirrors the on-the-wire shape of `/api/v1/security/profiles/...`. Lives next
to the ORM model as a sibling module to keep Pydantic-only types out of the
ORM file so SQLAlchemy imports stay slim.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.security_profile import DEFAULT_PRODUCT_RESOURCE_QUOTA

RiskClass = Literal["meta", "product-staging", "product-prod", "untrusted"]
NetworkPolicy = Literal["allow", "deny", "allowlist"]

# `material` lengths come straight from the spec, see
# docs/cockpit/veilig-bouwen-en-uitleveren.md §4.3.
ResourceQuota = dict[str, int]


def _quota_field() -> Field:
    return Field(default_factory=lambda: dict(DEFAULT_PRODUCT_RESOURCE_QUOTA))


def _egress_field() -> Field:
    return Field(default_factory=list)


class SecurityProfileBase(BaseModel):
    """Common payload shape shared by the create/update/patch schemas."""

    risk_class: RiskClass = "product-staging"
    default_transport: str = "sandcastle"
    default_skip_permissions: bool = False
    secrets_scope_id: str | None = None
    resource_quota: ResourceQuota = _quota_field()
    network_policy: NetworkPolicy = "allowlist"
    egress_allowlist: list[str] = _egress_field()


class SecurityProfileUpsert(SecurityProfileBase):
    """Body for `PUT /api/v1/security/profiles` (full replace)."""


class SecurityProfilePatch(BaseModel):
    """Body for `PATCH ...`. Every field optional; only set fields are applied."""

    risk_class: RiskClass | None = None
    default_transport: str | None = None
    default_skip_permissions: bool | None = None
    secrets_scope_id: str | None = None
    resource_quota: ResourceQuota | None = None
    network_policy: NetworkPolicy | None = None
    egress_allowlist: list[str] | None = None


class SecurityProfileResponse(SecurityProfileBase):
    """The full security profile returned by GET/PUT/PATCH."""

    project_path: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SecurityProfileDeleteResponse(BaseModel):
    """Body for `DELETE ...`. Carries the recreated default so the UI can show it."""

    project_path: str
    deleted: bool
    recreated_default: SecurityProfileResponse


def to_response(profile: Any) -> SecurityProfileResponse:
    """Serialize an ORM ``ProjectSecurityProfile`` row to the API schema."""
    return SecurityProfileResponse(
        project_path=profile.project_path,
        risk_class=profile.risk_class,
        default_transport=profile.default_transport,
        default_skip_permissions=profile.default_skip_permissions,
        secrets_scope_id=profile.secrets_scope_id,
        resource_quota=dict(profile.resource_quota),
        network_policy=profile.network_policy,
        egress_allowlist=list(profile.egress_allowlist or []),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
