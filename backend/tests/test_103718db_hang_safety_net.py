"""Live-fire verification for kaart 103718db...

This module verifies that ``scripts/run-single-test.sh --timeout-method=thread``
actually hard-kills a test that exhibits the asyncio-blocking-IO hang pattern
(the symptom ``UsageService.discover_jsonl_files`` exhibited against the real
``~/.claude/projects`` tree: tight sync I/O inside an ``async def`` that
starves the asyncio event loop indefinitely).

The module is gated by an environment variable — ``RUN_HANG_VERIFICATION=1`` —
that the script sets before invocation. Without that var, every test here is
skipped at collection time. This is belt-and-braces against plain
``pytest tests/`` runs ever hitting the hang path.

Run ONLY through the script:

    RUN_HANG_VERIFICATION=1 \\
    bash scripts/run-single-test.sh backend/tests/test_103718db_hang_safety_net.py

Expected behaviour:
- ``--timeout-method=thread`` → pytest-timeout's watchdog hard-kills pytest
  within ``-timeout`` seconds, the script exits non-zero (typically ``2`` —
  pytest's "interrupted" code), and ``elapsed_ms`` is within ~1s of
  ``TIMEOUT_SECONDS``.
- (With the pre-fix ``--timeout-method=signal``, pytest would run forever
  and the operator would have to ``kill -9`` by PID — the exact failure
  mode kaart 103718db... was filed against.)
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest


_RUN_HANG_VERIFICATION = os.environ.get("RUN_HANG_VERIFICATION") == "1"


if not _RUN_HANG_VERIFICATION:
    pytest.skip(
        "hang-safety-net verification — only runnable via "
        "scripts/run-single-test.sh with RUN_HANG_VERIFICATION=1",
        allow_module_level=True,
    )


@pytest.mark.asyncio
async def test_asyncio_blocking_busy_loop_is_killed_by_thread_timeout():
    """Tight CPU loop inside an ``async def`` — emulates the asyncio event
    loop starvation ``UsageService.discover_jsonl_files`` exhibited
    against the prod ``~/.claude/projects`` tree (sync ``Path.iterdir``
    inside an ``async`` coroutine blocks the loop indefinitely).

    With ``--timeout-method=thread``, the pytest-timeout watchdog sends
    SIGKILL and pytest exits with code 2 ("interrupted"). This is the
    documented 10s safety net actually firing — the bug the card
    describes.
    """
    while True:
        # No `await` — the loop never yields. SIGALRM never fires.
        sys.stdout.write("")  # noqa: B018
        sum(range(10_000))