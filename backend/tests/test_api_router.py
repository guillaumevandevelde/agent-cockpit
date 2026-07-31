"""Regression tests for v1 router registration."""

import warnings


def test_openapi_generation_emits_no_duplicate_operation_warnings():
    from app.main import app

    cached_schema = app.openapi_schema
    app.openapi_schema = None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            app.openapi()
    finally:
        app.openapi_schema = cached_schema

    duplicate_warnings = [
        warning
        for warning in caught
        if "Duplicate Operation ID" in str(warning.message)
    ]
    assert not duplicate_warnings
