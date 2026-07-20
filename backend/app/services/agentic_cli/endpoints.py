"""Project-scoped registry of Anthropic-compatible endpoints.

Each endpoint row carries only non-secret configuration: a stable name
(slug-style, used in pool entries + API paths), the upstream ``base_url``
the spawned CLI should point at, and the default ``model`` the CLI
should send. The credential (API key / bearer token) is **never** stored
here — ``provider_env.build_provider_env`` keeps its long-standing
contract of never resolving secrets, and this module reinforces that by
referencing credentials by name only (``credential_name``); the actual
value lookup is the caller's job (today: ``Settings`` for the
hardcoded MiniMax key; tomorrow: ``SecretStore.get(project_key,
credential_name)`` once follow-up #4 lands).

The shape mirrors the project's existing per-project key/value table
convention (``KanbanMeta``); the key prefix ``endpoint:<project_key>:``
keeps the namespace flat and discoverable without a schema migration —
same pattern as the active-subscription-override and the subscription
pool (see ``backend/app/kanban/subscription_pool.py``). Round-trip
serialization accepts the bare fields and tolerates legacy rows that
carry extra unknown keys (forward compat).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.kanban.models import KanbanMeta

logger = logging.getLogger(__name__)

ENDPOINT_PREFIX = "endpoint:"

# Fallback bucket for callers without a project context (e.g. the
# NewSessionDialog before the user picks a directory). Keeping it
# project-scoped (rather than truly global) keeps the on-disk layout
# uniform — every row is keyed by ``endpoint:<key>:<name>`` — and lets
# the dispatcher resolve per-card when the time comes.
DEFAULT_PROJECT_KEY = "_default"

# Endpoint names are slugs: lowercase letters, digits, dash, underscore.
# Reject anything else so a name is always safe to interpolate into a
# KanbanMeta key and a JSON file without escaping.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Endpoint:
    """One named Anthropic-compatible endpoint.

    Fields:
        name: stable slug; identifies the endpoint in pool entries and
            in API responses. Lowercase letters, digits, dash,
            underscore; max 64 chars; first char must be alphanumeric.
        base_url: the upstream URL the spawned CLI points at via
            ``ANTHROPIC_BASE_URL``. Required.
        model: the default model id the spawned CLI should send. Required.
        credential_name: optional name of a secret in the project's
            SecretStore. ``None`` means the CLI is expected to find the
            credential in its own ambient environment (matches MiniMax's
            host-env fallback). The actual credential value is never
            stored or returned by this module.
    """

    name: str
    base_url: str
    model: str
    credential_name: str | None = None


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"endpoint name must match {_NAME_RE.pattern!r}; got {name!r}",
        )


def _validate_nonempty(field: str, value: str | None) -> str:
    if not isinstance(value, str):
        raise ValueError(f"endpoint {field} must be a string; got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"endpoint {field} must not be empty")
    if "\n" in stripped or "\r" in stripped or "\x00" in stripped:
        raise ValueError(f"endpoint {field} must not contain newlines or null bytes")
    return stripped


def _key(project_key: str, name: str) -> str:
    return f"{ENDPOINT_PREFIX}{project_key}:{name}"


def _project_prefix(project_key: str) -> str:
    return f"{ENDPOINT_PREFIX}{project_key}:"


def serialize_endpoint(endpoint: Endpoint) -> str:
    """Validate + JSON-encode an endpoint row for storage."""
    _validate_name(endpoint.name)
    base_url = _validate_nonempty("base_url", endpoint.base_url)
    model = _validate_nonempty("model", endpoint.model)
    credential_name: str | None = None
    if endpoint.credential_name is not None:
        credential_name = _validate_nonempty("credential_name", endpoint.credential_name)
    return json.dumps({
        "name": endpoint.name,
        "base_url": base_url,
        "model": model,
        "credential_name": credential_name,
    })


def deserialize_endpoint(value: str) -> Endpoint | None:
    """Parse a stored JSON row. Returns ``None`` for a corrupt/legacy row
    that can't be salvaged — same fail-soft contract the subscription
    pool uses (``subscription_pool._deserialize_entries``).
    """
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        logger.warning("corrupt endpoint row; ignoring")
        return None
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("name")
    base_url = parsed.get("base_url")
    model = parsed.get("model")
    credential_name = parsed.get("credential_name")
    if not isinstance(name, str) or not isinstance(base_url, str) or not isinstance(model, str):
        return None
    if credential_name is not None and not isinstance(credential_name, str):
        return None
    # Surface invalid stored values as None so a corrupt row never breaks
    # the dispatcher; validation on write prevents new corruption.
    try:
        return Endpoint(
            name=name,
            base_url=base_url,
            model=model,
            credential_name=credential_name,
        )
    except (ValueError, TypeError):
        return None


async def list_endpoints(session, project_key: str) -> list[Endpoint]:
    """Return every endpoint stored for ``project_key``, sorted by name.

    An empty list means "no endpoints configured" — distinct from
    ``None`` (the subscription-pool convention) so the API layer can
    render an empty state without special-casing missing config.
    """
    prefix = _project_prefix(project_key)
    # KanbanMeta is small; fetch-all-then-filter is fine here (no scan
    # risk for a multi-row key/value table).
    rows = (await session.execute(
        # The kanban session has no "startswith" filter wired, so iterate
        # the small set explicitly. Migration to a dedicated table is a
        # future concern if the row count ever grows.
        __import__("sqlalchemy").select(KanbanMeta).where(KanbanMeta.key.like(prefix + "%")),
    )).scalars().all()
    out: list[Endpoint] = []
    for row in rows:
        endpoint = deserialize_endpoint(row.value)
        if endpoint is None:
            continue
        out.append(endpoint)
    out.sort(key=lambda e: e.name)
    return out


async def get_endpoint(session, project_key: str, name: str) -> Endpoint | None:
    """Return the named endpoint, or ``None`` when absent."""
    row = await session.get(KanbanMeta, _key(project_key, name))
    if row is None:
        return None
    return deserialize_endpoint(row.value)


async def upsert_endpoint(session, project_key: str, endpoint: Endpoint) -> None:
    """Insert or overwrite a single endpoint. Validation happens here so
    a corrupt row never lands in storage; raises ``ValueError`` to let
    the API layer surface a 422 instead of writing garbage.
    """
    serialized = serialize_endpoint(endpoint)
    key = _key(project_key, endpoint.name)
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=serialized))
    else:
        row.value = serialized
    await session.flush()


async def delete_endpoint(session, project_key: str, name: str) -> None:
    """Remove the named endpoint. No-op when absent (matches the
    subscription-pool contract — clearing a missing row is idempotent).
    """
    _validate_name(name)
    key = _key(project_key, name)
    row = await session.get(KanbanMeta, key)
    if row is None:
        return
    await session.delete(row)
    await session.flush()