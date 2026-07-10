"""Security tests for WebSocket origin validation in cc_bridge and agent_bridge.

Verifies that the _is_same_origin guard rejects loopback origins not in
settings.cors_origins, blocking cross-origin WebSocket attacks from other
local services.
"""
from unittest.mock import MagicMock, patch


def _make_ws(host: str) -> MagicMock:
    ws = MagicMock()
    ws.headers.get = lambda key, default="": {"host": host}.get(key, default)
    return ws


_ALLOWED = ["http://localhost:5173"]


class TestCcBridgeOriginValidation:
    def _check(self, origin: str, host: str, cors_origins: list[str] = _ALLOWED) -> bool:
        import app.api.v1.cc_bridge.router as mod
        with patch.object(mod, "settings") as m:
            m.cors_origins = cors_origins
            return mod._is_same_origin(origin, _make_ws(host))

    def test_same_origin_allowed(self):
        assert self._check("http://localhost:5173", "localhost:5173") is True

    def test_loopback_different_port_is_blocked(self):
        """127.0.0.1:8080 must not be accepted just because it is loopback."""
        assert self._check("http://127.0.0.1:8080", "localhost:5173") is False

    def test_cors_listed_origin_allowed_when_host_differs(self):
        """Origin in cors_origins is accepted even when Host header differs (proxy case)."""
        assert self._check("http://localhost:5173", "localhost:8000") is True

    def test_unlisted_loopback_port_blocked(self):
        assert self._check("http://localhost:9999", "localhost:5173") is False

    def test_ipv6_loopback_not_in_cors_blocked(self):
        assert self._check("http://[::1]:8080", "localhost:5173") is False

    def test_empty_origin_blocked(self):
        assert self._check("", "localhost:5173") is False

    def test_invalid_origin_blocked(self):
        assert self._check("not-a-url", "localhost:5173") is False

    def test_wildcard_cors_origin_does_not_bypass(self):
        """A literal '*' in cors_origins must not match arbitrary origins."""
        assert self._check("http://evil.example.com", "localhost:5173", cors_origins=["*"]) is False


class TestAgentBridgeOriginValidation:
    def _check(self, origin: str, host: str, cors_origins: list[str] = _ALLOWED) -> bool:
        import app.api.v1.runs.router as mod
        with patch.object(mod, "settings") as m:
            m.cors_origins = cors_origins
            return mod._is_same_origin(origin, _make_ws(host))

    def test_same_origin_allowed(self):
        assert self._check("http://localhost:5173", "localhost:5173") is True

    def test_loopback_different_port_is_blocked(self):
        assert self._check("http://127.0.0.1:8080", "localhost:5173") is False

    def test_cors_listed_origin_allowed_when_host_differs(self):
        assert self._check("http://localhost:5173", "localhost:8000") is True

    def test_unlisted_loopback_port_blocked(self):
        assert self._check("http://localhost:9999", "localhost:5173") is False

    def test_ipv6_loopback_not_in_cors_blocked(self):
        assert self._check("http://[::1]:8080", "localhost:5173") is False

    def test_empty_origin_blocked(self):
        assert self._check("", "localhost:5173") is False
