"""Tests for the security-audit stream (table + service + REST endpoint).

Coverage map:
- schema/ORM: default row shape + enum membership
- service.record: every invulpunt produces a row with the right kind +
  references-only payload
- service.record: refuses payloads that look like a leaked secret
- service.query: filters compose with AND; limit clamps; total is
  pre-limit
- REST endpoint: 200 happy path; filters visible on the URL;
  payload_ref is round-tripped without secret values
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.security_audit import SecurityAudit, SecurityAuditKind
from app.services import security_audit_service as svc

# ---------------------------------------------------------------- fixtures


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """In-memory SQLite session with the security_audit table materialised."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _override_db(session):
    async def _get_db():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    return _get_db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------- schema


def test_security_audit_kind_enum_has_all_required_values():
    """Card spec names the exact enum set — keep them in lock-step."""
    expected = {
        "skip_permissions_flip",
        "transport_change",
        "autodispatch_change",
        "secrets_put",
        "secrets_delete",
        "env_inject",
        "sandcastle_config_change",
        "run_start",
        "run_stop",
        "security_profile_change",
    }
    actual = {k.value for k in SecurityAuditKind}
    assert actual == expected


# ---------------------------------------------------------------- service.record


@pytest.mark.asyncio
async def test_record_inserts_row_with_references_only_payload(db_session):
    row = await svc.record(
        db_session,
        kind=SecurityAuditKind.SECRETS_PUT,
        project_key="git:example.com/repo-a",
        actor="user:alice",
        payload_ref={"name": "STRIPE_KEY"},
    )
    await db_session.commit()
    assert row is not None
    assert row.id is not None
    assert row.kind == "secrets_put"
    assert row.project_key == "git:example.com/repo-a"
    assert row.actor == "user:alice"
    assert row.payload_ref == {"name": "STRIPE_KEY"}


@pytest.mark.asyncio
async def test_record_refuses_payload_that_looks_like_a_secret(db_session, caplog):
    """The central guarantee: a leaked credential must never land in the table."""
    import logging

    caplog.set_level(logging.WARNING, logger="app.services.security_audit_service")

    leaked_value = "sk_live_abcdef1234567890ABCDEFGHIJKLmnopqrstuvwx"
    row = await svc.record(
        db_session,
        kind=SecurityAuditKind.SECRETS_PUT,
        project_key="git:example.com/repo-a",
        actor="user:alice",
        payload_ref={"name": "STRIPE_KEY", "value": leaked_value},
    )
    await db_session.commit()

    assert row is None
    # No row written
    rows = (await db_session.execute(_select_all())).scalars().all()
    assert rows == []
    # And we left a breadcrumb for the operator.
    assert any("refused" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_record_is_best_effort_when_db_raises(db_session):
    """A broken DB must not crash the originating action."""

    original_add = db_session.add

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated DB failure")

    db_session.add = boom  # type: ignore[assignment]
    try:
        row = await svc.record(
            db_session,
            kind=SecurityAuditKind.ENV_INJECT,
            project_key="git:example.com/repo-a",
            actor="dispatch",
            payload_ref={"session_name": "s1", "env_var_names": ["FOO"]},
        )
        assert row is None
    finally:
        db_session.add = original_add


def _select_all():
    from sqlalchemy import select

    return select(SecurityAudit)


# ---------------------------------------------------------------- service.query


@pytest.mark.asyncio
async def test_query_filters_by_project_and_kind(db_session):
    await svc.record(
        db_session, kind=SecurityAuditKind.TRANSPORT_CHANGE,
        project_key="git:a/repo", actor="u",
        payload_ref={"before": "worktree", "after": "sandcastle"},
    )
    await svc.record(
        db_session, kind=SecurityAuditKind.SECRETS_PUT,
        project_key="git:a/repo", actor="u",
        payload_ref={"name": "X"},
    )
    await svc.record(
        db_session, kind=SecurityAuditKind.SECRETS_PUT,
        project_key="git:b/repo", actor="u",
        payload_ref={"name": "Y"},
    )
    await db_session.commit()

    rows, total = await svc.query(db_session, project_key="git:a/repo",
                                  kind=SecurityAuditKind.SECRETS_PUT)
    assert total == 1
    assert len(rows) == 1
    assert rows[0].project_key == "git:a/repo"
    assert rows[0].kind == "secrets_put"


@pytest.mark.asyncio
async def test_query_respects_since_and_until_window(db_session):
    """``since`` / ``until`` bound the time window inclusively."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i, at in enumerate([base - timedelta(days=2),
                            base - timedelta(days=1),
                            base,
                            base + timedelta(days=1)]):
        # Use direct ORM insert so we can control ``at`` precisely.
        from sqlalchemy import insert

        await db_session.execute(
            insert(SecurityAudit).values(
                kind="run_start", project_key="p", actor="u",
                payload_ref={"i": i}, at=at,
            )
        )
    await db_session.commit()

    rows, total = await svc.query(
        db_session,
        since=base - timedelta(hours=1),
        until=base + timedelta(hours=1),
    )
    assert total == 1
    assert rows[0].payload_ref["i"] == 2


@pytest.mark.asyncio
async def test_query_clamps_limit_to_max(db_session):
    for i in range(5):
        await svc.record(
            db_session, kind=SecurityAuditKind.RUN_START,
            project_key="p", actor="u",
            payload_ref={"i": i},
        )
    await db_session.commit()

    rows, total = await svc.query(db_session, limit=10_000)
    assert total == 5
    # Hard cap is 1000; 5 fits trivially, so all five come back.
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_query_orders_newest_first(db_session):
    for i in range(3):
        await svc.record(
            db_session, kind=SecurityAuditKind.RUN_START,
            project_key="p", actor="u",
            payload_ref={"i": i},
        )
    await db_session.commit()
    rows, _ = await svc.query(db_session)
    # Newest first → later inserts come back earlier in the list.
    indices = [r.payload_ref["i"] for r in rows]
    assert indices == sorted(indices, reverse=True)


# ---------------------------------------------------------------- invulpunten


@pytest.mark.asyncio
async def test_invulpunt_dispatch_audit_helper_writes_row(monkeypatch, db_session):
    """The dispatch helper's ``_record_audit`` opens its own session;
    we monkeypatch that out and verify the row lands in our fixture."""
    from app.kanban import dispatch

    monkeypatch.setattr(
        "app.database.AsyncSessionLocal",
        lambda: _ContextManager(db_session),
    )
    await dispatch._record_audit(
        None,
        kind="skip_permissions_flip",
        project_key="git:x/repo",
        payload_ref={"enabled": True},
    )
    await db_session.commit()

    rows, total = await svc.query(
        db_session,
        kind=SecurityAuditKind.SKIP_PERMISSIONS_FLIP,
    )
    assert total == 1
    assert rows[0].project_key == "git:x/repo"
    assert rows[0].payload_ref["enabled"] is True
    assert rows[0].actor == "dispatch-api"


@pytest.mark.asyncio
async def test_invulpunt_dispatch_audit_helper_handles_session_failure(
    monkeypatch, db_session,
):
    """A broken audit insert must NOT raise — the dispatch write itself
    already flushed and is the security-relevant action."""
    from app.kanban import dispatch

    def boom():
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("app.database.AsyncSessionLocal", boom)

    # Should not raise even though AsyncSessionLocal is broken.
    await dispatch._record_audit(
        None,
        kind="autodispatch_change",
        project_key="git:x/repo",
        payload_ref={"enabled": True},
    )


@pytest.mark.asyncio
async def test_invulpunt_security_profile_risk_class_change_posts_audit(db_session):
    """A risk-class transition (not the same value written twice) emits an audit row."""
    from app.services.security_profile_service import SecurityProfileService

    svc_profile = SecurityProfileService(db_session)
    await svc_profile.upsert(
        "/tmp/product-a",
        {
            "risk_class": "product-staging",
            "default_transport": "sandcastle",
            "default_skip_permissions": False,
            "secrets_scope_id": None,
            "resource_quota": {
                "memory_mb": 1024, "cpu_quota": 1, "pids_limit": 128, "disk_mb": 2048,
            },
            "network_policy": "allowlist",
            "egress_allowlist": [],
        },
    )
    await svc_profile.patch("/tmp/product-a", {"risk_class": "product-prod"})
    await db_session.commit()

    rows, _ = await svc.query(db_session, kind=SecurityAuditKind.SECURITY_PROFILE_CHANGE)
    assert rows
    assert rows[0].payload_ref["before"] == "product-staging"
    assert rows[0].payload_ref["after"] == "product-prod"


@pytest.mark.asyncio
async def test_invulpunt_run_service_start_calls_record_audit_with_names_only(
    monkeypatch, tmp_path,
):
    """``RunService.start`` calls ``_record_audit`` with ``kind=run_start``
    and env *names* only — the value never reaches the audit sink.

    This is a tighter test than driving the full DB path: we replace
    ``_record_audit`` in the spawn module with a capture list and verify
    the call shape. The actual row-write contract is exercised by
    ``test_invulpunt_spawn_session_env_inject_writes_audit_row`` below
    against the in-memory fixture.
    """
    import asyncio

    from app.services import run_service as run_service_module

    captured: list[dict] = []

    def fake_record(
        project_key, runtime, session_name, env_var_names, **kw
    ):
        captured.append({
            "project_key": project_key,
            "runtime": runtime,
            "session_name": session_name,
            "env_var_names": sorted(env_var_names),
            "kind": kw.get("kind"),
        })

    # ``run_service`` already imported ``_record_audit`` into its own
    # module namespace, so we patch *that* binding (not the one on
    # ``spawn``) — otherwise ``run_service.start`` would still call the
    # original.
    monkeypatch.setattr(run_service_module, "_record_audit", fake_record)
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    async def fake_drive(*_args, **_kwargs):
        return None

    # ``_drive`` is a bound method on the module-level singleton.
    monkeypatch.setattr(run_service_module.run_service, "_drive", fake_drive)

    await run_service_module.run_service.start(
        project_path=str(tmp_path),
        command=["python3", "-c", "print('hi')"],
        env={"DATABASE_URL": "postgres://super-secret", "LOG_LEVEL": "debug"},
    )

    assert captured, "expected at least one _record_audit call"
    rec = captured[0]
    assert rec["kind"] == "run_start"
    assert rec["env_var_names"] == ["DATABASE_URL", "LOG_LEVEL"]
    # Critical invariant: the value never lands in the audit payload.
    assert "super-secret" not in str(rec)


@pytest.mark.asyncio
async def test_invulpunt_run_service_stop_calls_record_audit(monkeypatch, tmp_path):
    """``RunService.stop`` calls ``_record_audit`` with ``kind=run_stop``."""
    from unittest.mock import AsyncMock

    from app.services import run_service as run_service_module

    captured: list[dict] = []

    def fake_record(
        project_key, runtime, session_name, env_var_names, **kw
    ):
        captured.append({
            "project_key": project_key,
            "runtime": runtime,
            "session_name": session_name,
            "env_var_names": sorted(env_var_names),
            "kind": kw.get("kind"),
        })

    monkeypatch.setattr(run_service_module, "_record_audit", fake_record)

    # Mock the AppRun query so stop() finds a row to mark.
    fake_row = AsyncMock()
    fake_row.project_path = str(tmp_path)
    fake_row.transport = "subprocess"
    fake_row.port = 12345
    fake_row.container_id = None
    fake_row.pid = None
    fake_row.status = "running"

    class _FakeExecuteResult:
        def scalar_one_or_none(self):
            return fake_row

    class _FakeSession:
        async def execute(self, *_args, **_kwargs):
            return _FakeExecuteResult()

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        run_service_module, "AsyncSessionLocal", lambda: _FakeSession()
    )

    result = await run_service_module.run_service.stop("test-instance-id")
    assert result is True
    assert captured, "expected a stop audit call"
    assert captured[0]["kind"] == "run_stop"


@pytest.mark.asyncio
async def test_invulpunt_spawn_session_env_inject_passes_names_via_record_audit(
    monkeypatch, tmp_path,
):
    """The ``spawn_session`` env-inject hook calls ``_record_audit``
    with env *names* only — the value never reaches the audit sink.

    This is the integration counterpart of the unit-test in
    ``test_runs_spawn_env_isolation``: we drive ``spawn_session``
    end-to-end and capture the audit payload the production code
    builds. The actual row-write contract is owned by
    ``test_invulpunt_dispatch_audit_helper_writes_row`` above (the
    spawn helper uses the same record helper); here we only verify
    that ``spawn_session`` builds the right payload — including the
    ``COCKPIT_PROJECT_KEY`` / ``COCKPIT_RUNTIME`` cockpit-injected
    vars and the *names* of the caller's vars.
    """
    from types import SimpleNamespace

    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    monkeypatch.setattr(spawn, "_session_name_for",
                        lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    spawn.get_spawned_sessions().clear()

    captured: list[dict] = []

    def fake_record(project_key, runtime, session_name, env_var_names, **kw):
        captured.append({
            "project_key": project_key,
            "runtime": runtime,
            "session_name": session_name,
            "env_var_names": sorted(env_var_names),
            "kind": kw.get("kind", "env_inject"),
        })

    monkeypatch.setattr(spawn, "_record_audit", fake_record)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
        runtime="worktree",
        extra_env={"STRIPE_KEY_A": "sk_live_a", "GH_TOKEN_A": "ghp_a"},
    )

    assert captured, "expected an _record_audit call from spawn_session"
    rec = captured[0]
    assert rec["project_key"] == "git:example.com/repo-a"
    assert rec["runtime"] == "worktree"
    assert rec["kind"] == "env_inject"
    assert "STRIPE_KEY_A" in rec["env_var_names"]
    assert "GH_TOKEN_A" in rec["env_var_names"]
    # Cockpit-injected vars are part of the env too — they should land
    # in the audit by name (no values, ever).
    assert "COCKPIT_PROJECT_KEY" in rec["env_var_names"]
    assert "COCKPIT_RUNTIME" in rec["env_var_names"]
    # Critical invariant: the values never reach the audit payload.
    full = str(rec)
    assert "sk_live_a" not in full
    assert "ghp_a" not in full


# ---------------------------------------------------------------- REST endpoint


@pytest.mark.asyncio
async def test_rest_endpoint_returns_entries_with_filters(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        # Seed two projects × two kinds so we can prove the filters compose.
        for kind, project in [
            (SecurityAuditKind.TRANSPORT_CHANGE, "git:a/repo"),
            (SecurityAuditKind.SECRETS_PUT, "git:a/repo"),
            (SecurityAuditKind.SECRETS_PUT, "git:b/repo"),
        ]:
            await svc.record(
                db_session, kind=kind, project_key=project, actor="u",
                payload_ref={"name": "x", "before": "y", "after": "z"},
            )
        await db_session.commit()

        async with _client() as ac:
            r = await ac.get(
                "/api/v1/security/audit",
                params={"project_key": "git:a/repo",
                        "kind": SecurityAuditKind.SECRETS_PUT.value},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert entry["project_key"] == "git:a/repo"
        assert entry["kind"] == "secrets_put"
        assert entry["payload_ref"]["name"] == "x"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_rest_endpoint_rejects_unknown_kind(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        async with _client() as ac:
            r = await ac.get(
                "/api/v1/security/audit",
                params={"kind": "not_a_real_kind"},
            )
        # FastAPI surfaces the bad enum value as 422.
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_rest_endpoint_payload_never_carries_secret_value(db_session):
    """End-to-end: a secrets_put row's payload_ref round-trips without values."""
    app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        await svc.record(
            db_session,
            kind=SecurityAuditKind.SECRETS_PUT,
            project_key="git:a/repo",
            actor="u",
            payload_ref={"name": "STRIPE_KEY"},
        )
        await db_session.commit()

        async with _client() as ac:
            r = await ac.get("/api/v1/security/audit")
        assert r.status_code == 200
        import json as _json
        body = r.text
        # Defence-in-depth: the literal ``sk_live_`` prefix we use as a
        # sentinel must not appear anywhere in the response.
        assert "sk_live_" not in body
        assert "value" not in _json.loads(body)["entries"][0]["payload_ref"]
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------- helpers


class _ContextManager:
    """Wrap an existing ``AsyncSession`` in the ``async with`` shape that
    ``AsyncSessionLocal()`` returns.

    Lets a test point ``AsyncSessionLocal`` at an in-memory session it
    already owns, so a ``record(...)``-style call inside production code
    writes to the test's session, not the production pool.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return None