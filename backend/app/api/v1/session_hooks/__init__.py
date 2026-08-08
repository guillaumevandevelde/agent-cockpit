"""Session-lifecycle hook ingest + auto-resume endpoints.

These endpoints used to live under ``/scheduled-messages/``; the prefix
was renamed once the scheduled-messages feature (tmux injection) was
retired. The shared session substrate (idle state, auto-resume,
session-registry, signal pipeline) stayed; only its URL moved.
"""
