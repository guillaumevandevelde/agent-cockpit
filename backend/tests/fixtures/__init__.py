"""Test fixtures package for the cockpit backend.

Houses deterministic stand-ins for runtime providers (dispatch stub, etc.)
that the e2e + soak harness drives instead of a real Claude Code session.
The fixtures here are import-safe but runtime-agnostic: they never patch
production modules and they only talk to the backend over HTTP, so the
production backend, kanban DB, and dispatch tick all see the same wire
shape as a live spawn.
"""