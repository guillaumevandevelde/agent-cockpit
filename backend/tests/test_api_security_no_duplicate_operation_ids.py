"""Regression: FastAPI must not emit "Duplicate Operation ID" warnings when
the security router is registered. Five handlers (get_profile, put_profile,
patch_profile, delete_profile, list_audit) previously appeared twice on the
parent router — see kanban card 404ae28006ae4a70993c07d5329ce6fd — and each
one logged a UserWarning that polluted snapshot-gate output.
"""
from __future__ import annotations

import warnings

from app.main import app


def test_security_routes_have_unique_operation_ids() -> None:
    """Generating OpenAPI from the live app must not warn about duplicate IDs."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = app.openapi()

    dupes = [
        str(w.message)
        for w in caught
        if "Duplicate Operation ID" in str(w.message)
    ]
    assert dupes == [], f"unexpected duplicate-operation-id warnings: {dupes}"
