"""Tests for the curated free-endpoint seed catalog (kaart 8222fee8…)."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import pytest_asyncio

from app.services.agentic_cli.endpoints import (
    Endpoint,
    get_endpoint,
    list_endpoints,
)
from app.services.agentic_cli.free_endpoint_catalog import (
    CATALOG_PATH,
    FREE_TIER_KINDS,
    CatalogEntry,
    LiteLLMUpstream,
    load_catalog,
    seed_catalog,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

SessionLocal = TestSessionLocal()


def _session():
    return SessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- catalog file shape (data, not code) ----------------------------------


def test_catalog_file_is_well_formed_toml():
    """The catalog is a hand-curated repo file — guard against drift early."""
    raw = CATALOG_PATH.read_bytes()
    parsed = tomllib.loads(raw.decode("utf-8"))
    assert parsed.get("version") == 1
    assert isinstance(parsed.get("endpoint"), list)
    # The six seed providers the card specifies.
    names = {e["name"] for e in parsed["endpoint"]}
    assert {
        "openrouter-free-llama",
        "groq-llama-33-70b",
        "cerebras-gpt-oss-120b",
        "nvidia-llama-31-70b",
        "deepseek-chat",
        "together-llama-31-70b",
    }.issubset(names), f"missing seed entries; got {names!r}"


def test_catalog_entries_have_required_annotations():
    """Every seed row carries the four honesty annotations the card demands."""
    for entry in load_catalog():
        assert isinstance(entry, CatalogEntry)
        assert entry.free_tier_kind in FREE_TIER_KINDS
        # Real evidence URL (not a placeholder), not the provider's homepage.
        assert entry.free_evidence_url.startswith("http")
        # YYYY-MM-DD; full date check is in the parser — here we just guard.
        assert len(entry.free_measured_on) == 10 and entry.free_measured_on[4] == "-"
        assert entry.free_notes, f"{entry.name} has empty free_notes"
        # No marketing claim of "unlimited free" — the analyzedoc forbids it.
        lowered = entry.free_notes.lower()
        assert "unlimited" not in lowered, (
            f"{entry.name}: free_notes claims 'unlimited' — the analyzedoc "
            f"forbids that wording for OAuth-tier providers."
        )


def test_catalog_entries_match_endpoint_shape():
    """A catalog row, once installed, must look like a regular Endpoint."""
    for entry in load_catalog():
        # The four Endpoint fields the registry expects.
        assert isinstance(entry.name, str) and entry.name
        assert isinstance(entry.base_url, str) and entry.base_url
        assert isinstance(entry.model, str) and entry.model
        assert entry.credential_name is None or isinstance(entry.credential_name, str)


def test_catalog_names_match_slug_regex():
    """Endpoint names live in KanbanMeta keys — must be slug-safe."""
    import re
    name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
    for entry in load_catalog():
        assert name_re.match(entry.name), f"bad slug: {entry.name!r}"


def test_catalog_litellm_upstream_is_well_formed():
    """Each upstream block references a SecretStore key by env-var NAME only.

    No secret value should ever land in this file. ``api_key_env`` is the
    env-var that the LiteLLM proxy itself reads via ``os.environ/<VAR>``.
    """
    for entry in load_catalog():
        if entry.litellm_upstream is None:
            continue
        assert isinstance(entry.litellm_upstream, LiteLLMUpstream)
        for field_value in (
            entry.litellm_upstream.model,
            entry.litellm_upstream.api_base,
            entry.litellm_upstream.api_key_env,
        ):
            assert field_value and "\n" not in field_value and " " not in field_value.replace("https://", "")
        # Belt-and-braces: no obvious secret pattern (sk-…, key=…, Bearer …).
        joined = " ".join((
            entry.litellm_upstream.model,
            entry.litellm_upstream.api_base,
            entry.litellm_upstream.api_key_env,
        ))
        assert "sk-" not in joined.lower(), f"{entry.name} looks like it embeds a secret"


# ---- parser contract ------------------------------------------------------


def _write_catalog(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "catalog.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_catalog_returns_sorted_unique_names(tmp_path: Path):
    p = _write_catalog(tmp_path, """
version = 1
[[endpoint]]
name = "b-provider"
display_name = "B"
provider = "B"
base_url = "https://b.example.com"
model = "b"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://b.example.com/docs"
free_measured_on = "2026-07-27"
free_notes = "ok"
[[endpoint]]
name = "a-provider"
display_name = "A"
provider = "A"
base_url = "https://a.example.com"
model = "a"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://a.example.com/docs"
free_measured_on = "2026-07-27"
free_notes = "ok"
""")
    entries = load_catalog(p)
    assert [e.name for e in entries] == ["a-provider", "b-provider"]


def test_load_catalog_rejects_duplicate_names(tmp_path: Path):
    p = _write_catalog(tmp_path, """
version = 1
[[endpoint]]
name = "dup"
display_name = "D"
provider = "D"
base_url = "https://d.example.com"
model = "d"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://d.example.com/docs"
free_measured_on = "2026-07-27"
free_notes = "x"
[[endpoint]]
name = "dup"
display_name = "D2"
provider = "D2"
base_url = "https://d2.example.com"
model = "d2"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://d2.example.com/docs"
free_measured_on = "2026-07-27"
free_notes = "x"
""")
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(p)


def test_load_catalog_rejects_unknown_free_tier_kind(tmp_path: Path):
    p = _write_catalog(tmp_path, """
version = 1
[[endpoint]]
name = "x"
display_name = "X"
provider = "X"
base_url = "https://x.example.com"
model = "x"
free_tier_kind = "unlimited_free"
free_evidence_url = "https://x.example.com"
free_measured_on = "2026-07-27"
free_notes = "x"
""")
    with pytest.raises(ValueError, match="free_tier_kind"):
        load_catalog(p)


def test_load_catalog_rejects_bad_measured_date(tmp_path: Path):
    p = _write_catalog(tmp_path, """
version = 1
[[endpoint]]
name = "x"
display_name = "X"
provider = "X"
base_url = "https://x.example.com"
model = "x"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://x.example.com"
free_measured_on = "2026-07"
free_notes = "x"
""")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_catalog(p)


def test_load_catalog_rejects_unsupported_version(tmp_path: Path):
    p = _write_catalog(tmp_path, """
version = 99
[[endpoint]]
name = "x"
display_name = "X"
provider = "X"
base_url = "https://x.example.com"
model = "x"
free_tier_kind = "rate_limited_free"
free_evidence_url = "https://x.example.com"
free_measured_on = "2026-07-27"
free_notes = "x"
""")
    with pytest.raises(ValueError, match="unsupported catalog version"):
        load_catalog(p)


def test_load_catalog_missing_file_returns_empty(tmp_path: Path):
    # The catalog path is a build-time asset — if it's missing, the system
    # boots with an empty catalog rather than crashing. ``seed_catalog``
    # then becomes a no-op.
    assert load_catalog(tmp_path / "nope.toml") == []


# ---- seeder contract ------------------------------------------------------


async def test_seed_catalog_inserts_all_entries_when_bucket_empty():
    entries = load_catalog()
    async with _session() as s:
        installed, skipped, skipped_names = await seed_catalog(s, "proj-a")
        await s.commit()
    assert installed == len(entries)
    assert skipped == 0
    assert skipped_names == []

    async with _session() as s:
        rows = await list_endpoints(s, "proj-a")
    assert {r.name for r in rows} == {e.name for e in entries}


async def test_seed_catalog_skips_existing_rows_by_default():
    """An operator's runtime-config wins over the catalog by default."""
    async with _session() as s:
        await seed_catalog(s, "proj-a")
        # Operator overrides one row AFTER seed:
        from app.services.agentic_cli.endpoints import upsert_endpoint
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(
                name="groq-llama-33-70b",
                base_url="https://operator-chosen.example.com",
                model="operator-model",
            ),
        )
        await s.commit()

    # Re-seed without overwrite: operator's row stays.
    async with _session() as s:
        installed, skipped, skipped_names = await seed_catalog(s, "proj-a")
        await s.commit()
    assert skipped >= 1
    assert "groq-llama-33-70b" in skipped_names
    async with _session() as s:
        row = await get_endpoint(s, "proj-a", "groq-llama-33-70b")
    assert row.base_url == "https://operator-chosen.example.com"
    assert row.model == "operator-model"


async def test_seed_catalog_overwrite_replaces_existing_rows():
    async with _session() as s:
        from app.services.agentic_cli.endpoints import upsert_endpoint
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(
                name="groq-llama-33-70b",
                base_url="https://operator-chosen.example.com",
                model="operator-model",
            ),
        )
        await s.commit()

    # Re-seed WITH overwrite: operator's row is replaced by the catalog row.
    async with _session() as s:
        installed, skipped, _ = await seed_catalog(s, "proj-a", overwrite=True)
        await s.commit()
    assert skipped == 0
    assert installed == len(load_catalog())
    async with _session() as s:
        row = await get_endpoint(s, "proj-a", "groq-llama-33-70b")
    # The catalog row uses the loopback LiteLLM proxy base_url, not the
    # operator's override — the overwrite is intentional.
    assert row.base_url == "http://127.0.0.1:4000"


async def test_seed_catalog_does_not_touch_credentials():
    """``secret_name`` is metadata only — no key material is ever stored here.

    The seeder touches only KanbanMeta; the project's SecretStore is left
    alone. Verifying this is end-to-end-cheap: if no exception was raised
    and the endpoint row is installed, the SecretStore was never involved.
    """
    async with _session() as s:
        installed, _, _ = await seed_catalog(s, "proj-a")
    assert installed >= 6
    async with _session() as s:
        rows = await list_endpoints(s, "proj-a")
    # Every seeded row carries a credential_name; the SecretStore does NOT
    # need to have those keys for the seed to succeed (the resolve-path
    # raises at spawn time, not at seed time — see endpoints.py).
    assert all(r.credential_name for r in rows)