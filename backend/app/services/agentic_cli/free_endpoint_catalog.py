"""Curated seed-catalogus voor Anthropic-compatibele endpoints (gratis-tier).

Kaart `8222fee8…` (Endpoint-catalogus voor gratis-tier providers). Leest de
TOML-catalogus op ``backend/data/free_endpoint_catalog.toml`` en geeft de
resulterende ``CatalogEntry``-rijen terug, klaar om in een project's
endpoint-registry te installeren via ``endpoints.upsert_endpoint``.

Vorm van één entry (zie ``CatalogEntry``):

* ``name``             — slug, wordt ``endpoint.name`` in KanbanMeta. Volgt
                          dezelfde ``[a-z0-9_-]{1,64}``-regel als de
                          runtime-endpoints (geen escape-werk nodig).
* ``base_url``         — Anthropic-format URL waar Claude-Code op praat
                          (vrijwel altijd de LiteLLM-proxy op loopback).
* ``model``            — model-alias die de LiteLLM-proxy herkent
                          (vrijwel altijd == ``name``).
* ``credential_name``  — optionele naam van de SecretStore-sleutel.
                          Leeg = ambient-host-credential.
* ``provider``         — display-naam van de upstream.
* ``free_tier_kind``   — ``rate_limited_free`` / ``credits_then_paid`` /
                          ``free_with_topup`` / ``dev_tier_only``.
* ``free_evidence_url``— bewijslink (provider's eigen docs/rate-limit-pagina).
* ``free_measured_on`` — YYYY-MM-DD waarop de free-claim is geverifieerd.
* ``free_notes``       — eerlijke tekst over wat er gratis is en wat niet.
* ``litellm_upstream`` — optioneel ``(model, api_base, api_key_env)``-blok
                          dat letterlijk in een LiteLLM-``model_list`` hoort.

Bewust geen netwerk-I/O. De lijst is statisch (pinned) zodat een operator
exact weet wat er in het bestand staat en welke meetdatum de free-claim
heeft. Verversen = PR op deze file + ``free_measured_on`` ophogen.
"""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "free_endpoint_catalog.toml"

# Free-tier-soorten — vast en gesloten zodat een nieuwe variant een PR +
# catalog-uitbreiding afdwingt i.p.v. een vrij-tekst-veld dat over twee
# plekken uit sync kan lopen.
FREE_TIER_KINDS = frozenset({
    "rate_limited_free",
    "credits_then_paid",
    "free_with_topup",
    "dev_tier_only",
})


@dataclass(frozen=True)
class LiteLLMUpstream:
    """Eén regel voor de LiteLLM-``model_list`` die bij deze catalog-entry hoort.

    Geen secret-inhoud: ``api_key_env`` is de naam van de env-var die de
    proxy zelf uitleest (``os.environ/<VAR>``). De waarde komt uit de
    SecretStore / de host-env, nooit uit deze file.
    """

    model: str
    api_base: str
    api_key_env: str


@dataclass(frozen=True)
class CatalogEntry:
    """Eén Anthropic-compatibel endpoint uit de seed-catalogus."""

    name: str
    display_name: str
    provider: str
    base_url: str
    model: str
    credential_name: str | None
    free_tier_kind: str
    free_evidence_url: str
    free_measured_on: str
    free_notes: str
    litellm_upstream: LiteLLMUpstream | None


def _as_upstream(value: dict) -> LiteLLMUpstream:
    model = value.get("model")
    api_base = value.get("api_base")
    api_key_env = value.get("api_key_env")
    if not (isinstance(model, str) and model.strip()):
        raise ValueError("litellm_upstream.model must be a non-empty string")
    if not (isinstance(api_base, str) and api_base.strip()):
        raise ValueError("litellm_upstream.api_base must be a non-empty string")
    if not (isinstance(api_key_env, str) and api_key_env.strip()):
        raise ValueError("litellm_upstream.api_key_env must be a non-empty string")
    # We don't pin a URL scheme — providers differ (https://, http://127.0.0.1:4000).
    # Cheap sanity checks: no whitespace, no control chars, no newlines.
    for field_value in (model, api_base, api_key_env):
        if any(c in field_value for c in "\n\r\x00"):
            raise ValueError(f"litellm_upstream field contains control char: {field_value!r}")
    return LiteLLMUpstream(model=model, api_base=api_base, api_key_env=api_key_env)


def _parse_entry(raw: dict) -> CatalogEntry:
    name = raw.get("name")
    if not (isinstance(name, str) and name.strip()):
        raise ValueError("endpoint entry missing non-empty name")
    for required in ("display_name", "provider", "base_url", "model"):
        v = raw.get(required)
        if not (isinstance(v, str) and v.strip()):
            raise ValueError(f"{name!r}: missing non-empty {required}")
    free_kind = raw.get("free_tier_kind")
    if free_kind not in FREE_TIER_KINDS:
        raise ValueError(
            f"{name!r}: free_tier_kind must be one of {sorted(FREE_TIER_KINDS)}, "
            f"got {free_kind!r}",
        )
    evidence = raw.get("free_evidence_url")
    if not (isinstance(evidence, str) and evidence.strip()):
        raise ValueError(f"{name!r}: free_evidence_url required")
    measured = raw.get("free_measured_on")
    if not (isinstance(measured, str) and len(measured) == 10 and measured[4] == "-" and measured[7] == "-"):
        raise ValueError(f"{name!r}: free_measured_on must be YYYY-MM-DD, got {measured!r}")
    notes = raw.get("free_notes")
    if not isinstance(notes, str):
        raise ValueError(f"{name!r}: free_notes must be a string")
    cred = raw.get("credential_name")
    if cred is not None and not (isinstance(cred, str) and cred.strip()):
        raise ValueError(f"{name!r}: credential_name must be a non-empty string or null")
    upstream_raw = raw.get("litellm_upstream")
    upstream: LiteLLMUpstream | None
    if upstream_raw is None:
        upstream = None
    elif isinstance(upstream_raw, dict):
        upstream = _as_upstream(upstream_raw)
    else:
        raise ValueError(f"{name!r}: litellm_upstream must be a table or null")
    return CatalogEntry(
        name=name,
        display_name=raw["display_name"],
        provider=raw["provider"],
        base_url=raw["base_url"],
        model=raw["model"],
        credential_name=cred,
        free_tier_kind=free_kind,
        free_evidence_url=evidence,
        free_measured_on=measured,
        free_notes=notes.strip(),
        litellm_upstream=upstream,
    )


def load_catalog(path: Path | None = None) -> list[CatalogEntry]:
    """Parse de TOML-catalogus en geeft een gesorteerde lijst entries terug.

    Volgorde is op ``name`` zodat de output stabiel is tussen runs (handig
    voor tests + voor een UI-tabel die niet op invoegorde mag leunen).
    """
    catalog_path = path or CATALOG_PATH
    try:
        raw_bytes = catalog_path.read_bytes()
    except FileNotFoundError:
        logger.warning("free-endpoint catalog missing at %s", catalog_path)
        return []
    try:
        parsed = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        logger.error("free-endpoint catalog at %s is not valid TOML: %s", catalog_path, exc)
        raise
    version = parsed.get("version")
    if version != 1:
        raise ValueError(f"unsupported catalog version: {version!r} (expected 1)")
    raw_entries = parsed.get("endpoint")
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        raise ValueError("'endpoint' must be an array of tables")
    entries = [_parse_entry(e) for e in raw_entries]
    entries.sort(key=lambda e: e.name)
    # Name uniqueness — duplicates make kanban-key collisions on install.
    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            raise ValueError(f"duplicate catalog entry name: {entry.name!r}")
        seen.add(entry.name)
    return entries


async def seed_catalog(
    session,
    project_key: str,
    *,
    overwrite: bool = False,
    catalog_path: Path | None = None,
) -> tuple[int, int, list[str]]:
    """Installeer catalog-entries in de project-endpoint-registry.

    Retourneert ``(installed, skipped, skipped_names)``. Bij
    ``overwrite=False`` (default) wordt een bestaande endpoint met dezelfde
    naam onaangeraakt gelaten — een operator heeft de runtime-config
    wellicht bewust afwijkend van de catalogus. Bij ``overwrite=True`` wordt
    de bestaande rij vervangen door de catalog-rij (handig voor
    re-seeding na een catalog-update).

    Geen validatie van credentials — de catalog zegt alleen WAT er hoort te
    staan; de SecretStore-sleutel moet de operator zelf zetten.
    """
    from app.services.agentic_cli.endpoints import (
        Endpoint,
    )
    from app.services.agentic_cli.endpoints import (
        upsert_endpoint as _upsert,
    )

    entries = load_catalog(catalog_path)
    installed = 0
    skipped = 0
    skipped_names: list[str] = []
    for entry in entries:
        ep = Endpoint(
            name=entry.name,
            base_url=entry.base_url,
            model=entry.model,
            credential_name=entry.credential_name,
        )
        if not overwrite:
            from app.services.agentic_cli.endpoints import get_endpoint
            existing = await get_endpoint(session, project_key, entry.name)
            if existing is not None:
                skipped += 1
                skipped_names.append(entry.name)
                continue
        await _upsert(session, project_key, ep)
        installed += 1
    await session.flush()
    return installed, skipped, skipped_names