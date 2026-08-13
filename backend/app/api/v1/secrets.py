"""REST CRUD for project-scoped secrets.

Endpoints (under ``/api/v1/secrets/``):
- ``PUT    /{project_key}/{name}``      — upsert a secret; idempotent
- ``GET    /{project_key}/{name}``      — read a single secret (returns the value)
- ``DELETE /{project_key}/{name}``      — remove a secret
- ``GET    /?project_key=<key>``        — list names for a project (no values)

Why the list endpoint takes a query parameter instead of a path
segment: legitimate project keys contain ``/`` (e.g. ``git:github.com
/owner/repo``); routing them via a path segment + ``:path`` converter
makes the ``GET /{project_key}/{name}`` route steal the trailing
segment as a name — ``GET /secrets/git:github.com/owner/repo`` would
match the singular route with ``project_key=git:github.com/owner``
and ``name=repo``. A query parameter sidesteps the ambiguity.

Persistence lives in ``app.services.secrets_store.AGESecretStore``;
this module is a thin FastAPI shell. The store writes
``~/.claude-registry/secrets/<sanitized-project-key>.age`` with mode
``0o600``, encrypted under a symmetric passphrase (env-var or OS keyring).
See ``docs/features/secrets.md`` for the threat model and recovery
procedure.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.database import AsyncSessionLocal
from app.services.secrets_store import (
    AGESecretStore,
    AuthenticationError,
    ConfigurationError,
    SecretNotFound,
    SecretStoreError,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/secrets", tags=["Secrets"])


# -- factories (overridable in tests) ---------------------------------------


def _store() -> AGESecretStore:
    """Per-request store. Cheap to construct (no I/O until first CRUD call).

    Patched by ``tests/test_api_secrets.py`` to point at a tmp_path so
    tests never touch the production store. The passphrase is resolved
    by the store itself via ``app.services.secrets_store.resolve_passphrase``,
    which checks ``COCKPIT_SECRETS_PASSPHRASE`` then the OS keyring.
    """
    return AGESecretStore()


# -- schemas ----------------------------------------------------------------


# Names: allow env-var-style identifiers (letters, digits, underscore).
# Length is generous but bounded; FastAPI's path-param will reject
# empty by default.
_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,255}$"


class SecretPutRequest(BaseModel):
    value: str = Field(..., min_length=1, max_length=8192)


class SecretGetResponse(BaseModel):
    name: str
    value: str


class SecretListResponse(BaseModel):
    project_key: str
    names: list[str]


# -- helpers ----------------------------------------------------------------


def _validate_name(name: str) -> str:
    """Enforce a sane shape on secret names — the filesystem sanitizer
    would let almost anything through, but we want API callers to pick
    sensible env-var-style identifiers up front."""
    import re

    if not re.match(_NAME_PATTERN, name):
        raise HTTPException(
            status_code=400,
            detail=f"invalid secret name {name!r}: must match {_NAME_PATTERN}",
        )
    return name


def _unauth(e: SecretStoreError) -> HTTPException:
    """Map store-side authentication/configuration errors to HTTP 503.

    Both ``AuthenticationError`` (wrong passphrase) and
    ``ConfigurationError`` (no resolver) are operator-side problems,
    not client-side: the request itself was well-formed. 503 with
    Service Unavailable is the right signal.
    """
    logger.warning("secrets store unavailable: %s", e)
    return HTTPException(status_code=503, detail=str(e))


# -- routes -----------------------------------------------------------------
#
# Project keys can contain '/' (e.g. "git:github.com/owner/repo"); the
# ``:path`` converter lets Starlette match across slashes so the URL
# stays human-readable. The trailing ``/{name}`` segment is still a
# single segment because names can't contain '/' (enforced by
# _validate_name).


@router.put(
    "/{project_key:path}/{name}",
    response_model=SecretGetResponse,
    responses={
        400: {"description": "Invalid secret name"},
        503: {"description": "Passphrase missing or wrong"},
    },
)
async def put_secret(
    project_key: Annotated[str, Path(min_length=1, max_length=512)],
    name: Annotated[str, Path(min_length=1, max_length=256)],
    payload: SecretPutRequest,
) -> SecretGetResponse:
    """Insert or overwrite the secret ``name`` for ``project_key``.

    Idempotent: a repeated PUT replaces the previous value.
    """
    _validate_name(name)
    try:
        _store().put(project_key, name, payload.value)
    except AuthenticationError as e:
        raise _unauth(e)
    except ConfigurationError as e:
        raise _unauth(e)
    except SecretStoreError as e:
        logger.exception("put secret failed for %s/%s", project_key, name)
        raise HTTPException(status_code=500, detail=f"put failed: {e}")
    # Logged without the value (the store does the same internally).
    logger.info("secret upserted for project=%s name=%s", project_key, name)
    return SecretGetResponse(name=name, value=payload.value)


@router.get(
    "/{project_key:path}/{name}",
    response_model=SecretGetResponse,
    responses={
        404: {"description": "Secret not found"},
        503: {"description": "Passphrase missing or wrong"},
    },
)
async def get_secret(
    project_key: Annotated[str, Path(min_length=1, max_length=512)],
    name: Annotated[str, Path(min_length=1, max_length=256)],
) -> SecretGetResponse:
    """Read a single secret. Only this endpoint returns the value."""
    _validate_name(name)
    try:
        value = _store().get(project_key, name)
    except SecretNotFound:
        raise HTTPException(
            status_code=404,
            detail=f"no secret {name!r} for project_key={project_key!r}",
        )
    except AuthenticationError as e:
        raise _unauth(e)
    except ConfigurationError as e:
        raise _unauth(e)
    except SecretStoreError as e:
        logger.exception("get secret failed for %s/%s", project_key, name)
        raise HTTPException(status_code=500, detail=f"get failed: {e}")
    if value is None:
        # Project exists in the store but this name isn't in it.
        raise HTTPException(
            status_code=404,
            detail=f"no secret {name!r} for project_key={project_key!r}",
        )
    # Logged without the value.
    logger.info("secret read for project=%s name=%s", project_key, name)
    return SecretGetResponse(name=name, value=value)


@router.delete(
    "/{project_key:path}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "Secret not found"},
        503: {"description": "Passphrase missing or wrong"},
    },
)
async def delete_secret(
    project_key: Annotated[str, Path(min_length=1, max_length=512)],
    name: Annotated[str, Path(min_length=1, max_length=256)],
) -> None:
    """Remove the secret ``name`` from ``project_key``."""
    _validate_name(name)
    try:
        _store().delete(project_key, name)
    except SecretNotFound:
        raise HTTPException(
            status_code=404,
            detail=f"no secret {name!r} for project_key={project_key!r}",
        )
    except AuthenticationError as e:
        raise _unauth(e)
    except ConfigurationError as e:
        raise _unauth(e)
    except SecretStoreError as e:
        logger.exception("delete secret failed for %s/%s", project_key, name)
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")
    logger.info("secret deleted for project=%s name=%s", project_key, name)


@router.get("", response_model=SecretListResponse)
async def list_secrets(
    project_key: Annotated[
        str, Query(min_length=1, max_length=512, description="Project key to list secrets for")
    ],
) -> SecretListResponse:
    """List the names of all secrets for ``project_key``.

    No secret values are returned by this endpoint; use ``GET
    /{project_key}/{name}`` to read one. The ``project_key`` is a
    query parameter (not a path segment) because keys legitimately
    contain ``/`` and the PUT/GET/DELETE routes greedily match across
    slashes — see the module docstring.
    """
    try:
        names = _store().list(project_key)
    except AuthenticationError as e:
        raise _unauth(e)
    except ConfigurationError as e:
        raise _unauth(e)
    except SecretStoreError as e:
        logger.exception("list secrets failed for %s", project_key)
        raise HTTPException(status_code=500, detail=f"list failed: {e}")
    return SecretListResponse(project_key=project_key, names=names)