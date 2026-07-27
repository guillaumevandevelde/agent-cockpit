"""Every ``SpawnTransport`` sibling must accept the full protocol kwarg set.

``_run_card`` calls whichever transport ``get_transport_for_card`` picked with
one fixed kwarg set — it does not tailor the call per transport. So a kwarg
added to the ``SpawnTransport`` protocol (and to the call site) but forgotten on
a sibling transport is a ``TypeError`` at spawn time, not a type-check error:
the dispatcher retries the card ``MAX_DISPATCH_FAILURES`` times and parks it in
Impediment.

This has now happened twice:

- kaart 27317b4871… — ``endpoint_*`` added, resume transport not updated.
- kaart c31333bf… (commit 38a185c, RTK token-saver) — ``card_id`` /
  ``column_name`` added to the protocol + call site + worktree transport, but
  not to the resume, sandcastle and headless transports. Every resume dispatch
  died with ``_transport() got an unexpected keyword argument 'card_id'``,
  which stranded the whole "To Resume" column in Impediment.

The call site's own comment asserts the new kwargs are "a no-op for transports
that don't use them (resume, sandcastle, headless)" — this test is what makes
that comment true instead of aspirational. It reads the parameter names off the
protocol, so a future kwarg is covered without touching this file.
"""
import inspect

import pytest


def _protocol_kwarg_names() -> set[str]:
    from app.kanban.dispatch import SpawnTransport

    return {
        name
        for name in inspect.signature(SpawnTransport.__call__).parameters
        if name != "self"
    }


def _transports() -> dict[str, object]:
    from app.kanban.dispatch import (
        make_resume_transport,
        make_worktree_transport,
        sandcastle_transport,
    )
    from app.kanban.headless_runner import headless_transport

    return {
        "worktree": make_worktree_transport(),
        "resume": make_resume_transport("session-under-test"),
        "sandcastle": sandcastle_transport,
        "headless": headless_transport,
    }


@pytest.mark.parametrize("transport_name", sorted(_transports()))
def test_transport_accepts_every_spawn_transport_protocol_kwarg(transport_name):
    transport = _transports()[transport_name]
    params = inspect.signature(transport).parameters

    # A **kwargs catch-all absorbs anything the protocol grows later.
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return

    missing = _protocol_kwarg_names() - set(params)
    assert not missing, (
        f"{transport_name} transport is missing SpawnTransport kwarg(s) "
        f"{sorted(missing)}; _run_card passes them unconditionally, so every "
        f"dispatch routed to this transport raises TypeError and the card is "
        f"retried into Impediment."
    )
