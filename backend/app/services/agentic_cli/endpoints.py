"""Project-scoped registry of Anthropic-compatible endpoints.

Each endpoint row carries only non-secret configuration: a stable name
(slug-style, used in pool entries + API paths), the upstream ``base_url``
the spawned CLI should point at, and the default ``model`` the CLI
should send. The credential (API key / bearer token) is **never** stored
here — ``provider_env.build_provider_env`` keeps its long-standing
contract of never resolving secrets, and this module reinforces that by
referencing credentials by name only (``credential_name``); the actual
value lookup is the caller's job, resolved below in
``resolve_compatible_endpoint`` (``Settings.minimax_api_key`` for the
legacy MiniMax slot, ``SecretStore.get(project_key, credential_name)``
for everything else).

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
    # kaart 27317b4871… (FCR gap 6): a manual SQL edit / corrupt write
    # could leave an empty / whitespace-only base_url or model that
    # slips past the type check at line 130. ``build_provider_env``
    # would then raise a 3-retry ValueError at dispatch time. The
    # write-path validators (``_validate_nonempty``) catch this on
    # insert, but a hand-edited DB row needs the same defence in the
    # deserialiser — log + return None so the dispatcher falls through
    # to the project-key None path instead of crashing mid-spawn.
    if not name.strip() or not base_url.strip() or not model.strip():
        logger.warning(
            "endpoint row has empty name/base_url/model; ignoring "
            "(name=%r base_url=%r model=%r)",
            name, base_url, model,
        )
        return None
    if credential_name is not None and not credential_name.strip():
        logger.warning(
            "endpoint row has empty credential_name; ignoring (name=%r)",
            name,
        )
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


# ---- shared resolution helper (kaart 293d1faa…) ---------------------------
#
# The single place that turns an endpoint slug + DB session into the
# `(base_url, auth_token)` tuple ``build_provider_env`` requires.
# Both the interactive REST spawn (``api/v1/runs/router.py``) and the
# auto-dispatch path (``backend/app/kanban/dispatch.py``) call this so
# they cannot drift — earlier cards flagged the divergence as a
# duplication smell; centralising it here replaces two near-identical
# blocks and makes the validation surface obvious to the next reader.

# Provider constants are imported lazily inside ``resolve_compatible_endpoint``
# to avoid an import cycle (``provider_env`` has no DB deps and is imported
# very early in the app bootstrap).


def _secret_store():
    """Factory for the project-scoped ``SecretStore`` used by
    ``resolve_compatible_endpoint`` to look up non-MiniMax endpoint
    credentials. Kept as a module-level factory so tests can monkeypatch
    it (same pattern as ``backend/app/kanban/dispatch.py:_secret_store``).

    Lazy import keeps ``endpoints`` import-time free of the secrets_store
    module — the secret store is only needed for the non-MiniMax path
    anyway, and the import path would otherwise need to outlive the
    age / keyring transitive dependencies in some minimal test setups.
    """
    from app.services.secrets_store import AGESecretStore

    return AGESecretStore()

async def resolve_compatible_endpoint(
    session,
    project_key: str,
    endpoint_name: str | None,
    *,
    requested_model: str | None = None,
) -> dict | None:
    """Resolve an ``anthropic-compatible`` endpoint slug to the kwargs
    ``SpawnCommandOptions`` expects.

    Returns a dict with ``name``, ``base_url``, ``auth_token`` and
    ``model`` (always populated — falls back to the endpoint's own
    ``model`` when the caller didn't pin one, matching the
    interactive-path contract in
    ``api/v1/runs/router.py:543``). ``auth_token`` is ``None`` when
    the endpoint is configured with ``credential_name=None`` (caller
    is expected to find the credential in its own environment).

    Returns ``None`` when ``endpoint_name`` is falsy (caller has not
    pinned an endpoint — let the provider chain resolve the
    provider/model independently).

    Raises:
        ValueError: ``endpoint_name`` was supplied but no row with
            that slug exists in the project registry, OR the endpoint
            references a credential the backend cannot resolve.
            Both errors are surfaced upstream as the clean refusal
            the dispatcher wants (kaart 293d1faa… acceptance
            criterion: "fail-fast op configuratietijd … niet pas
            bij de derde mislukte dispatch").

    The REST handler converts a ``ValueError`` into an HTTP 400 with
    the helper's concrete message. The dispatch handler propagates
    the exception to the synchronously-failing spawn path so the
    card is bumped through the standard
    ``MAX_DISPATCH_FAILURES`` loop and lands in Impediment with the
    exact problem in the activity feed.
    """
    if not endpoint_name:
        return None
    endpoint = await get_endpoint(session, project_key, endpoint_name)
    if endpoint is None:
        raise ValueError(
            f"unknown endpoint {endpoint_name!r} for project {project_key!r}; "
            f"register it via /api/v1/agent-bridge/platforms/endpoints",
        )
    auth_token: str | None = None
    if endpoint.credential_name is None:
        # Ambient-credential pattern (e.g. the CLI's own
        # ANTHROPIC_AUTH_TOKEN from the host). The CLI may still find
        # one and use it; ``build_provider_env`` only sets
        # ``ANTHROPIC_AUTH_TOKEN`` when the token is non-empty.
        pass
    elif endpoint.credential_name == "minimax":
        # MVP credential resolution: only MiniMax's legacy ``.env``-
        # backed key is recognised here; the SecretStore-backed path
        # for every other credential lives just below.
        from app.config import settings
        auth_token = settings.minimax_api_key
        # kaart 27317b4871… (FCR gap 5): without this check the dispatch
        # path would silently spawn with ``ANTHROPIC_AUTH_TOKEN`` unset
        # and bill ambient-host credentials (or fall through to a 401
        # three retries later). The endpoint was deliberately registered
        # with ``credential_name='minimax'`` — that signal is lost if
        # the matching key is absent, so fail-fast at resolution time
        # with the exact remediation the operator can act on.
        if not auth_token:
            raise ValueError(
                f"endpoint {endpoint_name!r} requires credential "
                f"'minimax' but settings.minimax_api_key is not configured; "
                f"set MINIMAX_API_KEY in the backend environment or "
                f"re-register the endpoint with credential_name=None to "
                f"use the ambient-host credential instead.",
            )
    else:
        # kaart 333af652… — non-MiniMax credentials resolve via the
        # project's SecretStore. ``settings.minimax_api_key`` is a
        # legacy escape hatch for the only historically-recognised
        # key; every other provider (groq/9router/litellm/OpenRouter
        # free tier …) is expected to land a key in the project's
        # SecretStore via the per-project REST CRUD. A missing
        # SecretStore file (the project never set up a store) and a
        # SecretStore without that name both surface as ``None`` /
        # ``SecretNotFound`` here — we treat them the same way: the
        # credential is "not configured" and the caller gets a clear
        # 400 / dispatch-fail message naming the missing key.
        from app.services.secrets_store import SecretNotFound

        try:
            stored = _secret_store().get(project_key, endpoint.credential_name)
        except SecretNotFound:
            stored = None
        if stored:
            auth_token = stored
        else:
            raise ValueError(
                f"endpoint {endpoint_name!r} requires credential "
                f"{endpoint.credential_name!r}, which is not configured "
                f"in the project's SecretStore (or for legacy MiniMax "
                f"keys, settings.minimax_api_key is empty); set it via "
                f"POST /api/v1/secrets",
            )
    model = requested_model or endpoint.model
    return {
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "auth_token": auth_token,
        "model": model,
    }


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