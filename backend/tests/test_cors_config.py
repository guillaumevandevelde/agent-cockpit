"""CORS configuration must use explicit allowlists, not wildcards."""
from app.config import Settings


def test_cors_methods_no_wildcard():
    s = Settings()
    assert "*" not in s.cors_methods


def test_cors_methods_explicit_list():
    s = Settings()
    assert set(s.cors_methods) == {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}


def test_cors_headers_no_wildcard():
    s = Settings()
    assert "*" not in s.cors_headers


def test_cors_headers_explicit_list():
    s = Settings()
    assert set(s.cors_headers) == {"Content-Type", "Authorization", "X-API-Token"}
