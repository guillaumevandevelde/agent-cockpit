"""Tests for the project-scoped Anthropic-compatible endpoint registry."""
import json

import pytest
import pytest_asyncio

from app.kanban.models import KanbanMeta
from app.services.agentic_cli.endpoints import (
    ENDPOINT_PREFIX,
    Endpoint,
    delete_endpoint,
    deserialize_endpoint,
    get_endpoint,
    list_endpoints,
    serialize_endpoint,
    upsert_endpoint,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

SessionLocal = TestSessionLocal()


def _real_session():
    """Open a fresh DB session backed by the test DB.

    The endpoint-resolution tests use ``_session()`` (also bound to
    ``TestSessionLocal``) to seed rows; the spawn tests below need a
    *real* async session to hand to the FastAPI handler — FastAPI
    dependencies resolve ``db=object()`` to a bare ``object`` which
    has no ``.get()`` / ``.execute()`` surface.
    """
    return TestSessionLocal()()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _session():
    return SessionLocal()


# ---- pure (no DB) ---------------------------------------------------------


def test_serialize_roundtrip():
    ep = Endpoint(
        name="groq",
        base_url="https://api.groq.com/anthropic",
        model="llama-3.3-70b",
        credential_name="groq-key",
    )
    assert deserialize_endpoint(serialize_endpoint(ep)) == ep


def test_serialize_roundtrip_without_credential_name():
    ep = Endpoint(
        name="local-router",
        base_url="http://localhost:20128/v1",
        model="claude-sonnet-4",
    )
    assert deserialize_endpoint(serialize_endpoint(ep)).credential_name is None


def test_serialize_rejects_empty_base_url():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="x", base_url="   ", model="m"))


def test_serialize_rejects_empty_model():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="x", base_url="https://api.example.com", model=""))


def test_serialize_rejects_invalid_name():
    with pytest.raises(ValueError):
        serialize_endpoint(Endpoint(name="Invalid Name!", base_url="u", model="m"))


def test_serialize_rejects_newline_in_base_url():
    with pytest.raises(ValueError):
        serialize_endpoint(
            Endpoint(name="x", base_url="https://api.example.com\nFOO=bar", model="m"),
        )


def test_deserialize_corrupt_returns_none():
    assert deserialize_endpoint("not-json") is None


def test_deserialize_wrong_shape_returns_none():
    assert deserialize_endpoint(json.dumps([1, 2, 3])) is None


def test_deserialize_missing_field_returns_none():
    assert deserialize_endpoint(json.dumps({"name": "x", "base_url": "u"})) is None


def test_deserialize_tolerates_unknown_keys():
    raw = json.dumps({
        "name": "x", "base_url": "https://api.example.com", "model": "m",
        "future_field": 42,
    })
    ep = deserialize_endpoint(raw)
    assert ep is not None
    assert ep.name == "x"


# kaart 27317b4871… (FCR gap 6): the deserialiser used to type-check
# ``name`` / ``base_url`` / ``model`` but accept an empty string, so a
# hand-edited DB row could land ``base_url=""`` and crash dispatch 3
# retries later. Pin the empty-value rejection here so the
# defence-in-depth contract holds across refactors.


def test_deserialize_rejects_empty_base_url():
    raw = json.dumps({"name": "x", "base_url": "", "model": "m"})
    assert deserialize_endpoint(raw) is None


def test_deserialize_rejects_whitespace_only_base_url():
    raw = json.dumps({"name": "x", "base_url": "   \n", "model": "m"})
    assert deserialize_endpoint(raw) is None


def test_deserialize_rejects_empty_model():
    raw = json.dumps({"name": "x", "base_url": "https://api.example.com", "model": ""})
    assert deserialize_endpoint(raw) is None


def test_deserialize_rejects_empty_credential_name():
    raw = json.dumps({
        "name": "x", "base_url": "https://api.example.com", "model": "m",
        "credential_name": "   ",
    })
    assert deserialize_endpoint(raw) is None


# ---- DB-backed ------------------------------------------------------------


async def test_list_endpoints_empty_when_nothing_stored():
    async with _session() as s:
        assert await list_endpoints(s, "proj-a") == []


async def test_upsert_then_get_roundtrips():
    ep = Endpoint(
        name="groq",
        base_url="https://api.groq.com/anthropic",
        model="llama-3.3-70b",
        credential_name="groq-key",
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await s.commit()
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "groq") == ep


async def test_upsert_overwrites_existing_row():
    ep = Endpoint(name="x", base_url="https://old.example.com", model="m")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await s.commit()
    ep_v2 = Endpoint(name="x", base_url="https://new.example.com", model="m2")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep_v2)
        await s.commit()
    async with _session() as s:
        rows = (await s.execute(
            __import__("sqlalchemy").select(KanbanMeta).where(
                KanbanMeta.key == f"{ENDPOINT_PREFIX}proj-a:x"
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == serialize_endpoint(ep_v2)


async def test_list_endpoints_returns_sorted_and_skips_corrupt():
    async with _session() as s:
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="zeta", base_url="https://z.example.com", model="zm"),
        )
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="alpha", base_url="https://a.example.com", model="am"),
        )
        # Inject a corrupt row that list_endpoints must skip, not raise on.
        s.add(KanbanMeta(
            key=f"{ENDPOINT_PREFIX}proj-a:corrupt", value="not-json",
        ))
        await s.commit()

    async with _session() as s:
        eps = await list_endpoints(s, "proj-a")
    assert [e.name for e in eps] == ["alpha", "zeta"]


async def test_list_endpoints_isolates_by_project():
    async with _session() as s:
        await upsert_endpoint(
            s, "proj-a",
            Endpoint(name="x", base_url="https://a.example.com", model="ma"),
        )
        await upsert_endpoint(
            s, "proj-b",
            Endpoint(name="x", base_url="https://b.example.com", model="mb"),
        )
        await s.commit()
    async with _session() as s:
        a = await list_endpoints(s, "proj-a")
    async with _session() as s:
        b = await list_endpoints(s, "proj-b")
    assert len(a) == 1 and a[0].base_url == "https://a.example.com"
    assert len(b) == 1 and b[0].base_url == "https://b.example.com"


async def test_delete_endpoint_is_idempotent():
    async with _session() as s:
        # Missing row: no-op, no exception.
        await delete_endpoint(s, "proj-a", "absent")
        await s.commit()


# kaart 27317b4871… (FCR gap 5): when an endpoint is registered with
# ``credential_name='minimax'`` but ``settings.minimax_api_key`` is
# empty, ``resolve_compatible_endpoint`` must raise a clear ValueError
# naming the missing key instead of silently returning
# ``auth_token=None`` (which used to leak through to a 3-retry
# ``build_provider_env`` failure). Pin both the failure mode and the
# positive token-propagation case so a regression that drops the key
# between resolve and env-merge is caught here.


async def test_resolve_compatible_minimax_raises_when_api_key_missing(monkeypatch):
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-mx", Endpoint(
            name="router-mx", base_url="https://router-mx.example/v1",
            model="claude-sonnet-4-6", credential_name="minimax",
        ))
        await s.commit()
    # Force the configured key to None/empty without touching the
    # real Settings singleton — the resolve helper reads it lazily
    # on every call so we patch the attribute the resolver actually
    # reads (``app.config.settings``).
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "minimax_api_key", None)
    async with _session() as s:
        with pytest.raises(ValueError, match="minimax"):
            await resolve_compatible_endpoint(s, "proj-mx", "router-mx")


async def test_resolve_compatible_minimax_propagates_api_key(monkeypatch):
    """Positive: ``credential_name='minimax'`` with the key set →
    ``resolve_compatible_endpoint`` returns the exact auth_token in the
    resolver dict so ``build_provider_env`` can stamp it on
    ``ANTHROPIC_AUTH_TOKEN``. This is the regression guard for "the
    resolver drops the key between resolve and env-merge"."""
    from app import config as cfg_mod
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-mx", Endpoint(
            name="router-mx", base_url="https://router-mx.example/v1",
            model="claude-sonnet-4-6", credential_name="minimax",
        ))
        await s.commit()
    monkeypatch.setattr(cfg_mod.settings, "minimax_api_key", "sk-minimax-test")
    async with _session() as s:
        resolved = await resolve_compatible_endpoint(s, "proj-mx", "router-mx")
    assert resolved == {
        "name": "router-mx",
        "base_url": "https://router-mx.example/v1",
        "auth_token": "sk-minimax-test",
        "model": "claude-sonnet-4-6",
    }


async def test_resolve_compatible_ambient_credential_returns_none_token():
    """``credential_name=None`` (ambient) → ``auth_token`` is None so
    ``build_provider_env`` knows to skip setting
    ``ANTHROPIC_AUTH_TOKEN`` and let the host-env credential (if any)
    be picked up by the spawned CLI."""
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-amb", Endpoint(
            name="router-amb", base_url="https://router-amb.example/v1",
            model="claude-sonnet-4-6", credential_name=None,
        ))
        await s.commit()
    async with _session() as s:
        resolved = await resolve_compatible_endpoint(s, "proj-amb", "router-amb")
    assert resolved["auth_token"] is None
    assert resolved["base_url"] == "https://router-amb.example/v1"


async def test_resolve_compatible_unknown_credential_name_raises():
    """A ``credential_name`` the resolver doesn't recognise (anything
    other than ``None`` / ``'minimax'`` today) raises ValueError so the
    caller surfaces the misconfiguration as a clean 422 instead of
    letting the spawn loop through with an undefined auth_token."""
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    async with _session() as s:
        await upsert_endpoint(s, "proj-unknown", Endpoint(
            name="router-unknown", base_url="https://x.example/v1",
            model="m", credential_name="some-future-secret",
        ))
        await s.commit()
    async with _session() as s:
        with pytest.raises(ValueError, match="some-future-secret"):
            await resolve_compatible_endpoint(s, "proj-unknown", "router-unknown")
    ep = Endpoint(name="x", base_url="https://x.example.com", model="m")
    async with _session() as s:
        await upsert_endpoint(s, "proj-a", ep)
        await delete_endpoint(s, "proj-a", "x")
        await s.commit()
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "x") is None
    # Second delete is still a no-op.
    async with _session() as s:
        await delete_endpoint(s, "proj-a", "x")
        await s.commit()


async def test_get_endpoint_returns_none_when_absent():
    async with _session() as s:
        assert await get_endpoint(s, "proj-a", "nope") is None


# kaart 333af652… — non-MiniMax ``credential_name`` resolves via the
# project's SecretStore (not a hardcoded MiniMax-only branch). The
# earlier implementation raised ValueError unconditionally for any
# credential_name != "minimax", so a groq/9router/litellm endpoint
# with its own SecretStore row could never be spawned. Wire the
# resolver through ``AGESecretStore.get(project_key, name)`` and
# keep the MiniMax legacy fallback so the existing MiniMax flow
# is untouched.


class _FakeSecretStore:
    """Minimal SecretStore stub for endpoint-resolver tests.

    Mirrors the ``get(project_key, name) -> str | None`` contract from
    ``app/services/secrets_store.py``: returns the value when present,
    ``None`` when the file exists but the name is absent. Tests inject
    this via ``monkeypatch.setattr`` on the resolver's private factory
    so the resolver never touches the real on-disk AGE file.
    """

    def __init__(self, mapping: dict[tuple[str, str], str | None]):
        self._mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def get(self, project_key: str, name: str) -> str | None:
        self.calls.append((project_key, name))
        return self._mapping.get((project_key, name))


async def test_resolve_compatible_secret_store_credential_returns_token(monkeypatch):
    """A ``credential_name`` that's in the project's SecretStore resolves
    to the stored value: ``auth_token`` is the SecretStore payload, not
    ``None``. This is the "groq/9router/litellm work out of the box"
    acceptance criterion from kaart 333af652 — the resolver must not
    hardcode ``'minimax'`` as the only recognised credential name."""
    from app.services.agentic_cli import endpoints as ep_mod
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    fake = _FakeSecretStore({("proj-groq", "groq-key"): "gsk-groq-test"})
    monkeypatch.setattr(ep_mod, "_secret_store", lambda: fake)
    async with _session() as s:
        await upsert_endpoint(s, "proj-groq", Endpoint(
            name="router-groq", base_url="https://api.groq.com/anthropic",
            model="llama-3.3-70b", credential_name="groq-key",
        ))
        await s.commit()
    async with _session() as s:
        resolved = await resolve_compatible_endpoint(s, "proj-groq", "router-groq")
    assert resolved["auth_token"] == "gsk-groq-test"
    assert resolved["base_url"] == "https://api.groq.com/anthropic"
    assert resolved["model"] == "llama-3.3-70b"
    assert fake.calls == [("proj-groq", "groq-key")]


async def test_resolve_compatible_secret_store_credential_not_present_raises(monkeypatch):
    """A ``credential_name`` that has no row in the project's SecretStore
    still raises ValueError — the resolver must not silently fall back
    to ``auth_token=None`` (which would spawn the CLI anonymously and
    401 three retries later). The error message names the missing
    credential so the operator knows exactly what to configure."""
    from app.services.agentic_cli import endpoints as ep_mod
    from app.services.agentic_cli.endpoints import (
        resolve_compatible_endpoint,
        upsert_endpoint,
    )
    fake = _FakeSecretStore({})  # SecretStore exists, but no rows match
    monkeypatch.setattr(ep_mod, "_secret_store", lambda: fake)
    async with _session() as s:
        await upsert_endpoint(s, "proj-missing", Endpoint(
            name="router-missing", base_url="https://router-missing.example/v1",
            model="m", credential_name="missing-key",
        ))
        await s.commit()
    async with _session() as s:
        with pytest.raises(ValueError, match="missing-key"):
            await resolve_compatible_endpoint(s, "proj-missing", "router-missing")


# kaart 333af652… — the spawn REST handler used to resolve the
# endpoint against the shared ``_default`` bucket regardless of where
# the row was registered. An endpoint created via
# ``POST /platforms/endpoints?project_key=myproject`` appeared in
# ``GET /platforms/endpoints?project_key=myproject`` but 404'd at
# spawn time. Honour the same ``project_key`` query parameter on
# ``POST /sessions`` so the three endpoints stay symmetric.


@pytest.mark.asyncio
async def test_spawn_resolves_endpoint_under_project_key_query_param(
    monkeypatch, tmp_path,
):
    from app.api.v1.runs import router as agent_bridge_api
    from app.services.agentic_cli import endpoints as ep_mod
    from app.services.agentic_cli.endpoints import upsert_endpoint

    fake = _FakeSecretStore({("myproject", "groq-key"): "gsk-spawn-test"})
    monkeypatch.setattr(ep_mod, "_secret_store", lambda: fake)
    monkeypatch.setattr(agent_bridge_api, "spawn_session", lambda *a, **k: {"cli": "claude-code"})

    async with _session() as s:
        await upsert_endpoint(s, "myproject", Endpoint(
            name="groq",
            base_url="https://api.groq.com/anthropic",
            model="llama-3.3-70b",
            credential_name="groq-key",
        ))
        await s.commit()

    response = await agent_bridge_api.spawn_session_endpoint(
        agent_bridge_api.SpawnRequest(
            cli="claude-code",
            directory=str(tmp_path),
            provider="anthropic-compatible",
            endpoint_name="groq",
        ),
        project_key="myproject",
        db=_real_session(),
    )
    assert response["cli"] == "claude-code"


@pytest.mark.asyncio
async def test_spawn_falls_back_to_default_bucket_when_no_project_key(
    monkeypatch, tmp_path,
):
    """No ``project_key`` on the spawn → default bucket. Preserves the
    pre-existing behaviour for the NewSessionDialog that resolves
    endpoints from the shared default row."""
    from app.api.v1.runs import router as agent_bridge_api
    from app.services.agentic_cli.endpoints import upsert_endpoint

    monkeypatch.setattr(agent_bridge_api, "spawn_session", lambda *a, **k: {"cli": "claude-code"})

    async with _session() as s:
        await upsert_endpoint(s, "_default", Endpoint(
            name="shared-groq",
            base_url="https://api.groq.com/anthropic",
            model="llama-3.3-70b",
        ))
        await s.commit()

    # ``project_key`` omitted entirely → handler must default to
    # ``_default`` (matches list/upsert/delete's ``project_key or
    # DEFAULT_PROJECT_KEY`` pattern).
    response = await agent_bridge_api.spawn_session_endpoint(
        agent_bridge_api.SpawnRequest(
            cli="claude-code",
            directory=str(tmp_path),
            provider="anthropic-compatible",
            endpoint_name="shared-groq",
        ),
        project_key=None,  # FastAPI's Query(default=None) is the Query
        # object itself when the function is called directly without the
        # dependency-injection machinery; pass None explicitly so the
        # `or DEFAULT_PROJECT_KEY` fallback actually fires.
        db=_real_session(),
    )
    assert response["cli"] == "claude-code"


# kaart 333af652… — the status endpoint
# (``GET /platforms/endpoints`` → ``_credential_configured``) used to
# hardcode ``False`` for every non-MiniMax credential, so the
# NewSessionDialog kept claiming "Credential X is not configured"
# even after the operator had stored the key via
# ``POST /api/v1/secrets``. The spawn path already resolves via
# the project's SecretStore; the status endpoint must mirror that
# so the UI's "configured" hint is honest. Pin the four cases that
# together form the fix: missing name, legacy MiniMax (env-backed),
# SecretStore hit, SecretStore miss. The MiniMax branch must not
# regress — the legacy escape-hatch is intentional.


class _FakeSecretStoreForStatus:
    """Strict-shape SecretStore stub for the status-endpoint tests.

    Mirrors the public ``get(project_key, name) -> str | None`` contract
    from ``app/services/secrets_store.py`` AND raises the same
    ``SecretNotFound`` when the project has no file at all (the real
    store's distinction between "file exists, name absent" and
    "file absent" — both surface as ``not configured`` in the UI,
    but the underlying store has to handle them differently so the
    test matches the real contract).
    """

    def __init__(self, mapping: dict[tuple[str, str], str]):
        self._mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def get(self, project_key: str, name: str) -> str | None:
        from app.services.secrets_store import SecretNotFound

        if (project_key, name) not in self._mapping:
            raise SecretNotFound(
                f"no secret {name!r} for project_key={project_key!r}",
            )
        self.calls.append((project_key, name))
        return self._mapping[(project_key, name)]


def test_credential_configured_returns_false_for_none_name():
    from app.api.v1.runs import router as agent_bridge_api

    assert agent_bridge_api._credential_configured(None, "proj-a") is False


def test_credential_configured_legacy_minimax_uses_settings(monkeypatch):
    """``credential_name == 'minimax'`` stays on the legacy
    ``settings.minimax_api_key`` path — the existing legacy escape
    hatch must not be broken by the new SecretStore wiring."""
    from app import config as cfg_mod
    from app.api.v1.runs import router as agent_bridge_api

    monkeypatch.setattr(cfg_mod.settings, "minimax_api_key", "sk-minimax-legacy")
    assert agent_bridge_api._credential_configured("minimax", "proj-a") is True


def test_credential_configured_legacy_minimax_empty_returns_false(monkeypatch):
    from app import config as cfg_mod
    from app.api.v1.runs import router as agent_bridge_api

    monkeypatch.setattr(cfg_mod.settings, "minimax_api_key", None)
    assert agent_bridge_api._credential_configured("minimax", "proj-a") is False


def test_credential_configured_secret_store_hit_returns_true(monkeypatch):
    """A non-MiniMax ``credential_name`` that has a row in the
    project's SecretStore returns ``True`` — the status indicator
    stops lying after the operator PUTs the key via
    ``POST /api/v1/secrets``."""
    from app.api.v1.runs import router as agent_bridge_api

    fake = _FakeSecretStoreForStatus({("proj-groq", "groq-key"): "gsk-groq"})
    monkeypatch.setattr(agent_bridge_api, "_secret_store", lambda: fake)
    assert agent_bridge_api._credential_configured("groq-key", "proj-groq") is True
    assert fake.calls == [("proj-groq", "groq-key")]


def test_credential_configured_secret_store_miss_returns_false(monkeypatch):
    """A ``credential_name`` that's not in the SecretStore returns
    ``False`` (and never raises) — the UI keeps showing
    "Credential X is not configured" without 500'ing the request."""
    from app.api.v1.runs import router as agent_bridge_api

    fake = _FakeSecretStoreForStatus({})
    monkeypatch.setattr(agent_bridge_api, "_secret_store", lambda: fake)
    assert agent_bridge_api._credential_configured("missing-key", "proj-groq") is False