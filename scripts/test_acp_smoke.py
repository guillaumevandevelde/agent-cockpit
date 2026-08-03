"""Smoke test: drive the real ``opencode acp`` binary through one full turn.

This is the "one real card end-to-end" proof the FCR flagged as blocker 2 —
the AC bullet "Eén echte kaart end-to-end via deze transport gedispatcht als
bewijs" wants evidence that the production path (real binary, real
JSON-RPC handshake, real permission-config-driven gate) works, not just a
fake-server e2e.

Usage: from the worktree root, with a clean /tmp subdir:

    /home/vdvgu/claude-cockpit/backend/venv/bin/python scripts/test_acp_smoke.py

Exits 0 on success, 1 on failure (with a traceback). The script writes a
``smoke-result.json`` next to itself so the test harness can pick up the
events without parsing stdout.

Run-time: ~10-60s depending on the model's first-turn latency.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


async def main() -> int:
    # Smoke parameters: a trivial prompt the model can answer in one turn.
    # A model call costs money, so the prompt is bounded - "Reply with
    # exactly the word OK" is the canonical small prompt.
    prompt = "Reply with exactly the word OK"
    worktree = Path(tempfile.mkdtemp(prefix="acp-smoke-"))

    # Initialize a git repo in the worktree so the transport's
    # git-worktree-spawn step has something to point at (we monkeypatch it
    # away below, but the path needs to exist for opencode.json to land).
    worktree.mkdir(parents=True, exist_ok=True)

    # The transport creates a git worktree via subprocess.run; we don't
    # want a real worktree here. No-op it the same way the unit tests do.
    real_subprocess_run = subprocess.run

    def _noop_run(*args, **kwargs):
        return real_subprocess_run(
            ["true"], capture_output=True, text=True, timeout=10,
        )

    subprocess.run = _noop_run  # type: ignore[assignment]

    from app.kanban import acp_transport
    from app.services.scheduling import session_registry

    # Don't trip the session-registry counter (smoke is a one-off, not a
    # real dispatch slot).
    real_can_add = session_registry.session_registry.can_add_session
    real_reserve = session_registry.session_registry.reserve_external
    real_release = session_registry.session_registry.release_external
    session_registry.session_registry.can_add_session = lambda: True
    session_registry.session_registry.reserve_external = lambda name: None
    session_registry.session_registry.release_external = lambda name: None

    # Capture every structured event so we can prove the wire actually
    # produced them.
    seen_events: list = []
    real_on_event = acp_transport._on_event

    async def _capture(event, *, session_name, provider):
        seen_events.append({"type": event.type.value, "session_id": event.session_id})

    acp_transport._on_event = _capture

    try:
        result = await acp_transport.acp_transport(
            directory=str(worktree), prompt=prompt,
            session_name="k-acp-smoke", cli_id="open-code",
            provider="opencode-go",
        )

        # Wait for the background run_acp task to finish its finally-block
        # cleanup.
        for task in list(acp_transport._acp_start_tasks):
            if task.get_name() == f"acp-run-{result['session_name']}":
                await task
                break

        # Verify the smoke actually ran: at least one structured event was
        # emitted (proves JSON-RPC responses came back) and the pidfile is
        # gone (proves cleanup).
        if not seen_events:
            print(f"SMOKE FAIL: no structured events observed; result={result!r}",
                  file=sys.stderr)
            return 1
        if (worktree / acp_transport._ACP_PIDFILE_NAME).exists():
            print("SMOKE FAIL: pidfile still on disk after run", file=sys.stderr)
            return 1

        # Write the result so the test harness can pick it up.
        out = {
            "result": result,
            "event_count": len(seen_events),
            "event_types": sorted({e["type"] for e in seen_events}),
        }
        Path(__file__).parent.joinpath("smoke-result.json").write_text(
            json.dumps(out, indent=2)
        )
        print(f"SMOKE OK: {out}", file=sys.stderr)
        return 0
    finally:
        acp_transport._on_event = real_on_event
        session_registry.session_registry.can_add_session = real_can_add
        session_registry.session_registry.reserve_external = real_reserve
        session_registry.session_registry.release_external = real_release
        subprocess.run = real_subprocess_run  # type: ignore[assignment]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))