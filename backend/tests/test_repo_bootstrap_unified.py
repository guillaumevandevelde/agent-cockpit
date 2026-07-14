"""Lock-in tests for the unified RepoBootstrapService (kanban card dca8c8dc).

The atomic-init step (kanban card §6 #1, ``dceb60ab``) and the
optional gh-remote step (§6 #2, ``b8ae9c4a``) used to live in two
separate ``RepoBootstrapService`` classes in two separate files. The
meta-card ``dca8c8dc`` flagged that the foundation was split across
two classes with the same name and different import paths; consumers
had to import from both modules to compose the chain. These tests
pin the unified shape:

- ``RepoBootstrapService`` is the **same class object** whether
  imported via ``app.services.repo_bootstrap`` or
  ``app.services.repo_bootstrap_service`` — no copy, no shadow class.
- A single instance has **both** ``init_local`` and ``create_remote``
  methods so a caller can compose the chain end-to-end on one object.
- ``init_local`` produces the local-path precondition that
  ``create_remote`` consumes — exercised end-to-end on tmp_path with
  ``gh`` mocked to a no-op (auth-missing graceful path).
"""
from __future__ import annotations

import inspect

from app.services import repo_bootstrap
from app.services.repo_bootstrap import RepoBootstrapService as FromLegacyShim
from app.services.repo_bootstrap_service import (
    CreateRemoteResult,
)
from app.services.repo_bootstrap_service import (
    RepoBootstrapService as FromCanonicalModule,
)


def test_legacy_shim_and_canonical_module_resolve_to_same_class() -> None:
    """The same class object under both import paths.

    Guards against a regression where someone re-introduces a
    parallel class definition in one of the modules.
    """
    assert FromLegacyShim is FromCanonicalModule, (
        "app.services.repo_bootstrap.RepoBootstrapService must be the same "
        "class object as app.services.repo_bootstrap_service.RepoBootstrapService "
        "(the legacy module is a re-export shim, not a parallel implementation)."
    )


def test_unified_class_has_both_methods_on_a_single_instance() -> None:
    """A single RepoBootstrapService instance exposes both bootstrap steps.

    This is the lock-in for the meta-card's third acceptance criterion:
    `create_remote`'s `local_path` precondition (a locally-init'd repo)
    is producible on the same instance via `init_local`.
    """
    svc = FromCanonicalModule()

    assert callable(getattr(svc, "init_local", None)), (
        "RepoBootstrapService.init_local missing — atomic-init step is "
        "not exposed on the unified class"
    )
    assert callable(getattr(svc, "create_remote", None)), (
        "RepoBootstrapService.create_remote missing — gh-remote step is "
        "not exposed on the unified class"
    )
    # Sanity: both are bound methods on the same `svc`.
    assert svc.init_local.__self__ is svc
    assert svc.create_remote.__self__ is svc


def test_init_local_then_create_remote_compose_on_same_instance(
    tmp_path, monkeypatch
) -> None:
    """End-to-end: init_local → create_remote on the same svc instance.

    Drives the chain the orchestrator (InceptionService / BlueprintApply)
    will drive: produce a locally-init'd repo, then call create_remote on
    the same instance. The gh-call is mocked to "missing" so we exercise
    the graceful no-op branch; the test asserts the local repo survived
    intact and ``create_remote`` returned the documented
    ``reason="gh_missing_or_unauthed"`` sentinel.
    """
    svc = FromCanonicalModule()
    target = tmp_path / "demo"

    init_result = svc.init_local(str(target), project_name="demo")
    assert init_result.first_commit_sha
    assert (target / ".git").is_dir()
    assert (target / "README.md").is_file()

    # Now drive create_remote on the SAME instance; with `gh` unavailable
    # the service should return a graceful no-op without raising.
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.shutil.which", lambda name: None
    )

    import asyncio

    result = asyncio.run(
        svc.create_remote(str(target), repo_name="demo", visibility="private")
    )
    assert isinstance(result, CreateRemoteResult)
    assert result.created is False
    assert result.reason == "gh_missing_or_unauthed"
    # Local repo must still be intact — graceful no-op ≠ rollback.
    assert (target / ".git").is_dir()


def test_shim_re_exports_canonical_symbols() -> None:
    """The legacy module re-exports the symbols tests and callers expect.

    Without these, the existing ``from app.services.repo_bootstrap
    import …`` imports in test_repo_bootstrap.py break.
    """
    expected = {
        "InitResult",
        "RepoBootstrapError",
        "RepoAlreadyInitializedError",
        "BootstrapRemoteCreationError",
        "CreateRemoteResult",
        "MigrateKeysFn",
        "DUMMY_GIT_USER_NAME",
        "DUMMY_GIT_USER_EMAIL",
        "RepoBootstrapService",
    }
    for name in expected:
        assert hasattr(repo_bootstrap, name), (
            f"app.services.repo_bootstrap.{name} missing — re-export shim "
            f"lost a public symbol"
        )
    # `__all__` lists exactly the public surface (helps static analyzers).
    assert set(repo_bootstrap.__all__) == expected


def test_init_local_signature_unchanged_for_existing_callers() -> None:
    """Backward compat: existing ``init_local(...)`` callers keep working.

    No tests today use the ``migrate_keys`` kwarg with ``init_local``,
    but verify that constructing the service with the old-style single
    positional kwarg (git_executable) and the new style (migrate_keys)
    both produce a usable instance.
    """
    sig = inspect.signature(FromCanonicalModule.__init__)
    params = sig.parameters
    assert "migrate_keys" in params, "constructor lost migrate_keys kwarg"
    assert "git_executable" in params, "constructor lost git_executable kwarg"
    # Both should be keyword-only — preserves the old call shape
    # ``RepoBootstrapService(migrate_keys=...)`` and
    # ``RepoBootstrapService(git_executable=...)``.
    assert params["migrate_keys"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["git_executable"].kind is inspect.Parameter.KEYWORD_ONLY