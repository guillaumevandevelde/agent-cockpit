"""Tests for the structured-signal registry (`session_signals.py`) and the
typed-signal fast paths it enables.

Three small test groups here:

  1. `SessionSignalRegistry` direct behaviour — record/lookup/clear of
     SessionStart + Notification(limit) signals. The contract is what the
     reaper and delivery engine rely on, so we exercise it directly rather
     than only through integration.
  2. `wait_for_pane_ready` structured-signal fast path — when a SessionStart
     is recorded before the call, the function returns immediately without
     polling the tmux pane; when it isn't, the function falls back to the
     existing box-drawing-char pane scan (the original behaviour, kept for
     the interactive path).
  3. `_structured_rate_limit_signal` reaper helper — when the typed signal
     is recorded the function returns True regardless of pane content,
     keeping the reaper's fail-open semantics for sessions whose `claude`
     process never even reached the hook stage.
"""
import asyncio

import pytest

from app.services.scheduling import tmux_inject
from app.services.scheduling.session_signals import (
    SessionSignalRegistry,
    session_name_for_dispatched_cwd,
    session_signals,
)

# ---------- 1. SessionSignalRegistry direct behaviour -------------------------


def test_name_from_cwd_accepts_worktree_path():
    """Kanban-dispatched sessions run in `<project>/.claude/worktrees/<name>` —
    that's the shape we recognise, anything else returns None so we never
    attribute a hook from a hand-started `claude` to our dispatch sweep."""
    assert session_name_for_dispatched_cwd(
        "/home/me/proj/.claude/worktrees/k-feature-abc"
    ) == "k-feature-abc"


def test_name_from_cwd_rejects_other_shapes():
    """The dispatch sweep must NOT pick up the project root, an arbitrary
    `claude` session, or a sandcastle container path. Each returns None —
    leaving a hook from any of these shapes to be a no-op for the registry."""
    assert session_name_for_dispatched_cwd(None) is None
    assert session_name_for_dispatched_cwd("") is None
    assert session_name_for_dispatched_cwd("/home/me/proj") is None
    # worktrees subdir but missing .claude parent — not ours
    assert session_name_for_dispatched_cwd(
        "/home/me/proj/notclaude/worktrees/k-feature-abc"
    ) is None
    # .claude/worktrees but the cwd is `worktrees` itself, not a child
    assert session_name_for_dispatched_cwd(
        "/home/me/proj/.claude/worktrees"
    ) is None


def test_record_started_ignores_unrecognised_cwd():
    """A SessionStart hook from a non-dispatched cwd must not pollute the
    registry — record_started returns None and the registry stays empty."""
    reg = SessionSignalRegistry()
    assert reg.record_started("/home/me/proj") is None
    assert reg.is_started("proj") is False
    assert reg._started == set()


def test_record_limit_ignores_unrecognised_cwd():
    """Same fail-open as record_started: a Notification hook from a
    hand-started `claude` session (cwd not under .claude/worktrees) must
    not get registered as "rate-limited" for some random name."""
    reg = SessionSignalRegistry()
    assert reg.record_limit("/home/me/proj", "API Error: 429") is None
    assert reg.is_rate_limited("proj") is False
    assert reg._limits == {}


def test_record_started_round_trips():
    """record_started marks the session; is_started returns True. The function
    returns the session name (so callers can log it) and is idempotent — a
    second SessionStart (CC fires them per prompt) is a no-op, not a clear."""
    reg = SessionSignalRegistry()
    name = reg.record_started("/p/.claude/worktrees/k-rs-0001")
    assert name == "k-rs-0001"
    assert reg.is_started("k-rs-0001") is True
    # second call returns same name, doesn't error or duplicate
    assert reg.record_started("/p/.claude/worktrees/k-rs-0001") == "k-rs-0001"
    assert reg._started == {"k-rs-0001"}


def test_record_limit_round_trips_with_message():
    """record_limit keeps the canonical CC notification message so a future
    audit log can surface it directly. limit_message returns the same value
    for a successful round trip; is_rate_limited returns True."""
    reg = SessionSignalRegistry()
    msg = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    name = reg.record_limit("/p/.claude/worktrees/k-rl-0001", msg)
    assert name == "k-rl-0001"
    assert reg.is_rate_limited("k-rl-0001") is True
    assert reg.limit_message("k-rl-0001") == msg


def test_record_limit_first_write_wins():
    """The same limit hit may fire twice (Notification hook + the new
    background-agent re-emit); first-write-wins keeps the audit trail
    stable and prevents a re-emit with a slightly different wording from
    overwriting the canonical capture."""
    reg = SessionSignalRegistry()
    reg.record_limit("/p/.claude/worktrees/k-rl-0002", "first message")
    reg.record_limit("/p/.claude/worktrees/k-rl-0002", "second message (ignored)")
    assert reg.limit_message("k-rl-0002") == "first message"


def test_clear_drops_all_signals():
    """The dispatch kill path calls clear() so a re-spawn under the same
    tmux session name doesn't inherit the previous occupant's "rate-limited"
    or "started" flags. After clear, both predicates return False and the
    message is forgotten."""
    reg = SessionSignalRegistry()
    reg.record_started("/p/.claude/worktrees/k-cl-0001")
    reg.record_limit("/p/.claude/worktrees/k-cl-0001", "boom")
    assert reg.is_started("k-cl-0001") is True
    assert reg.is_rate_limited("k-cl-0001") is True
    reg.clear("k-cl-0001")
    assert reg.is_started("k-cl-0001") is False
    assert reg.is_rate_limited("k-cl-0001") is False
    assert reg.limit_message("k-cl-0001") is None


def test_clear_unknown_name_is_noop():
    """clear() on a name we never recorded must not error — the kill path
    fires unconditionally and we don't want stray-session kills to surface
    KeyError or ValueError to the caller."""
    reg = SessionSignalRegistry()
    reg.clear("never-existed")  # must not raise


@pytest.mark.asyncio
async def test_wait_until_started_returns_true_when_already_recorded():
    """If the SessionStart was recorded before the wait started, the await
    resolves True without blocking — the fast-path that lets delivery inject
    keystrokes immediately when CC is already up."""
    reg = SessionSignalRegistry()
    reg.record_started("/p/.claude/worktrees/k-wu-0001")
    assert await reg.wait_until_started("k-wu-0001", timeout_s=0.1) is True


@pytest.mark.asyncio
async def test_wait_until_started_resolves_on_record():
    """The await must wake the moment the SessionStart is recorded — not on
    the next poll tick. We simulate the hook-endpoint side: spawn a waiter,
    give it a beat, then record. If the waiter hadn't been woken, the
    timeout would fire and the test would fail."""
    reg = SessionSignalRegistry()
    waiter = asyncio.create_task(
        reg.wait_until_started("k-wu-0002", timeout_s=2.0)
    )
    await asyncio.sleep(0.01)  # let the waiter register itself
    reg.record_started("/p/.claude/worktrees/k-wu-0002")
    assert await waiter is True


@pytest.mark.asyncio
async def test_wait_until_started_times_out_when_no_signal():
    """Without a record, the await times out cleanly (False, no exception)
    so the caller can fall back to pane scraping or give up. timeout_s=0
    short-circuits to False immediately — useful for callers that want a
    non-blocking check."""
    reg = SessionSignalRegistry()
    assert await reg.wait_until_started("k-wu-0003", timeout_s=0.05) is False
    assert await reg.wait_until_started("k-wu-0003", timeout_s=0) is False
    # empty/blank session name short-circuits as well — the caller passed
    # something we can't track, no point waiting on nothing.
    assert await reg.wait_until_started("", timeout_s=0.1) is False


# ---------- 2. wait_for_pane_ready structured-signal fast path ----------------


@pytest.mark.asyncio
async def test_wait_for_pane_ready_returns_immediately_when_session_started(monkeypatch):
    """When the SessionStart hook already fired for the session embedded in
    the tmux target, wait_for_pane_ready must short-circuit (no pane
    capture, no box-drawing-char check) and return True after settle_s.
    This is the high-leverage path the card is about: a live session that
    simply needs keystrokes injected doesn't need any pane polling."""
    # direct import of session_signals singleton
    session_signals.clear("k-fp-0001")
    session_signals.record_started("/p/.claude/worktrees/k-fp-0001")
    captured = []

    def must_not_run(*a, **kw):
        captured.append((a, kw))
        raise AssertionError("pane should not be polled when signal is set")

    monkeypatch.setattr(tmux_inject, "_capture_pane", must_not_run)
    ready = await tmux_inject.wait_for_pane_ready(
        "k-fp-0001:0.0", timeout_s=2.0, poll_s=0.05, settle_s=0.0,
    )
    assert ready is True
    assert captured == [], "_capture_pane must not be called when the SessionStart signal is set"
    session_signals.clear("k-fp-0001")


@pytest.mark.asyncio
async def test_wait_for_pane_ready_falls_back_to_pane_when_no_signal(monkeypatch):
    """When no SessionStart signal is recorded, wait_for_pane_ready must use
    the original box-drawing-char pane scan. This is the fail-open path that
    keeps the interactive tmux path working for sessions whose `claude`
    process hasn't yet emitted any hook — same observable behaviour as
    before the refactor."""
    mod_signals = session_signals
    mod_signals.clear("k-fp-0002")
    monkeypatch.setattr(
        tmux_inject, "_capture_pane", lambda *a, **kw: "╭─ prompt ─╰",
    )
    ready = await tmux_inject.wait_for_pane_ready(
        "k-fp-0002:0.0", timeout_s=2.0, poll_s=0.05, settle_s=0.0,
    )
    assert ready is True
    mod_signals.clear("k-fp-0002")


@pytest.mark.asyncio
async def test_wait_for_pane_ready_picks_up_signal_during_poll(monkeypatch):
    """A SessionStart may arrive *during* the poll loop (typical: spawn at
    t=0ms, CC fires SessionStart at t=80ms, poll cadence is 500ms). The
    function must short-circuit on the next tick without waiting for the
    pane to render — otherwise the wait would be paced by tmux capture-pane
    cadence instead of the actual signal arriving."""
    mod_signals = session_signals
    mod_signals.clear("k-fp-0003")
    calls = {"n": 0}

    def fake_capture(tmux_target):
        calls["n"] += 1
        # On the third capture, simulate the SessionStart arriving by
        # recording it just before returning. Subsequent polls must see it.
        if calls["n"] == 3:
            mod_signals.record_started("/p/.claude/worktrees/k-fp-0003")
        return "booting..."  # never renders the input frame

    monkeypatch.setattr(tmux_inject, "_capture_pane", fake_capture)
    ready = await tmux_inject.wait_for_pane_ready(
        "k-fp-0003:0.0", timeout_s=2.0, poll_s=0.02, settle_s=0.0,
    )
    assert ready is True
    # We don't assert exact call count (asyncio scheduling adds jitter), but
    # we *do* assert that we returned well before the timeout, i.e. the
    # signal short-circuit fired — not the timeout.
    assert calls["n"] < 100, f"too many captures ({calls['n']}); signal wasn't picked up"
    mod_signals.clear("k-fp-0003")


def test_session_name_from_target_handles_pane_suffix():
    """`spawn_for` returns targets like '<session>:0.0'; the helper must
    extract just the session name so the signal-registry lookup hits."""
    assert tmux_inject._session_name_from_target("k-sess:0.0") == "k-sess"
    # Defensive: a bare session name (no `:`) round-trips unchanged; an
    # empty string round-trips empty.
    assert tmux_inject._session_name_from_target("k-sess") == "k-sess"
    assert tmux_inject._session_name_from_target("") == ""


# ---------- 3. _structured_rate_limit_signal reaper helper --------------------


def test_structured_rate_limit_signal_reflects_registry(monkeypatch):
    """_structured_rate_limit_signal must defer to session_signals.is_rate_limited
    — a True there returns True here, a False there returns False here.
    Verified by going through the module-level singleton via clear+record."""
    import app.kanban.dispatch as d
    session_signals.clear("k-sr-0001")
    assert d._structured_rate_limit_signal("k-sr-0001") is False
    session_signals.record_limit("/p/.claude/worktrees/k-sr-0001", "boom")
    assert d._structured_rate_limit_signal("k-sr-0001") is True
    session_signals.clear("k-sr-0001")


def test_kill_agent_session_clears_signals(monkeypatch):
    """The dispatch kill path must clear the structured-signal registry for
    the same reason it clears SessionRegistry.clear_spawn — a re-spawn under
    the same tmux name must not inherit the previous occupant's signals.
    Without this clear, a fresh spawn could be misclassified on its very
    first tick as already-started or rate-limited."""
    import app.kanban.dispatch as d
    # No-op the actual tmux call so we don't need a live tmux server for the test.
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **kw: None)
    session_signals.record_started("/p/.claude/worktrees/k-kl-0001")
    session_signals.record_limit("/p/.claude/worktrees/k-kl-0001", "boom")
    assert session_signals.is_started("k-kl-0001") is True
    assert session_signals.is_rate_limited("k-kl-0001") is True
    d._kill_agent_session("k-kl-0001")
    assert session_signals.is_started("k-kl-0001") is False
    assert session_signals.is_rate_limited("k-kl-0001") is False
