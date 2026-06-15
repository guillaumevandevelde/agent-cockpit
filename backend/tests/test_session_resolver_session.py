from unittest.mock import patch
from app.services.scheduling import session_resolver as sr
from app.services.scheduling.session_resolver import resolve_session_target, AMBIGUOUS
from app.services.scheduling.session_registry import SessionRegistry


def _panes(*items):
    # items: (pane_id, cwd, target)
    return [{"pane_id": p, "cwd": c, "tmux_target": t} for p, c, t in items]


def test_known_pane_alive_returns_target():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%3", "/proj", "win:0.0"))):
        assert resolve_session_target("s1", "/proj") == "win:0.0"


def test_known_pane_gone_returns_none():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions", return_value=_panes()):
        assert resolve_session_target("s1", "/proj") is None


def test_cold_registry_zero_panes_returns_none():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions", return_value=_panes()):
        assert resolve_session_target("s1", "/proj") is None


def test_cold_registry_single_pane_returns_target():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%9", "/proj", "win:0.0"))):
        assert resolve_session_target("s1", "/proj") == "win:0.0"


def test_cold_registry_multiple_panes_returns_ambiguous():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%1", "/proj", "a:0.0"),
                                          ("%2", "/proj", "b:0.0"))):
        assert resolve_session_target("s1", "/proj") is AMBIGUOUS
