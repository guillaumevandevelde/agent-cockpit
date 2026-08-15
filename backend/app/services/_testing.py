"""Test-only helpers for clearing in-memory state on module-level singletons.

The conftest's per-test ``drop_all`` + ``create_all`` resets the DB to a fresh
schema, which resets auto-increment ids back to 1 every test. Singletons in
``app/services/*.py`` keep per-id state on the instance (e.g. rate-limit
windows, cooldown timestamps) — without clearing them between tests, the new
test's row id collides with leftover entries from a previous test.

Adding a per-file autouse fixture works locally but the list is scattered
across the codebase and easy to miss when a new service gains per-id state.
Centralise the reset here so the conftest's per-test fixture calls one place.

When adding a new service with per-id state, import the singleton below and
clear its per-id dict here — same shape as ``agent_mail_service``.
"""
from app.services.agent_mail_service import agent_mail_service


def reset_all_singleton_test_state() -> None:
    """Clear in-memory per-id state on every module-level singleton.

    Called by ``backend/tests/conftest.py:_reset_singleton_state`` between
    tests so each test sees a clean singleton state matching its clean DB.
    """
    agent_mail_service._last_auto_nudge_at.clear()