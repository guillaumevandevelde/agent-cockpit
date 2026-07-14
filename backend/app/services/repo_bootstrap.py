"""Backwards-compatible re-export shim for the unified RepoBootstrapService.

The atomic-init step (kanban card §6 #1, ``dceb60ab``) and the
optional gh-remote step (§6 #2, ``b8ae9c4a``) used to live in two
separate ``RepoBootstrapService`` classes in two separate files. They
are now unified in ``app.services.repo_bootstrap_service`` — a single
class with both ``init_local`` and ``create_remote`` methods.

This module preserves the old import path so existing callers and
tests keep working::

    from app.services.repo_bootstrap import (
        InitResult, RepoAlreadyInitializedError,
        RepoBootstrapError, RepoBootstrapService,
    )

The class itself lives in
``app.services.repo_bootstrap_service.RepoBootstrapService``; the import
below is the same object, not a copy. See that module's docstring for
the full design rationale.
"""
from app.services.repo_bootstrap_service import (  # noqa: F401
    DUMMY_GIT_USER_EMAIL,
    DUMMY_GIT_USER_NAME,
    BootstrapRemoteCreationError,
    CreateRemoteResult,
    InitResult,
    MigrateKeysFn,
    RepoAlreadyInitializedError,
    RepoBootstrapError,
    RepoBootstrapService,
)

__all__ = [
    "BootstrapRemoteCreationError",
    "CreateRemoteResult",
    "DUMMY_GIT_USER_EMAIL",
    "DUMMY_GIT_USER_NAME",
    "InitResult",
    "MigrateKeysFn",
    "RepoAlreadyInitializedError",
    "RepoBootstrapError",
    "RepoBootstrapService",
]