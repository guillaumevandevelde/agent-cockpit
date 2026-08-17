"""Deterministic dispatch stub for the e2e + soak harness.

Replaces the real Claude Code provider session with a fixed script that
posts a branch deliverable and moves the card to Done, so the dispatch
lifecycle can be exercised end-to-end without burning provider quota
or waiting on a real spawn.

Spawned by:

  - ``frontend/e2e/dispatch/lifecycle.spec.ts`` (Playwright — S1..S5).
  - ``backend/scripts/cockpit_soak.py`` (nightly soak under the canonical
    ``minimax`` provider).

Wire contract (matches ``backend/app/api/v1/kanban/router.py``):

  - ``POST /api/v1/kanban/cards/{cid}/deliverables`` with
    ``{"kind": "branch", "ref": "k-e2e-<run_id>"}``
  - ``POST /api/v1/kanban/cards/{cid}/move`` with
    ``{"column": "Done", "summary": "<stub summary>"}``

Both endpoints enforce the summary gate; the stub summary is short enough
to satisfy the 40-word leesbaarheidsnorm without further work.

CLI usage::

    python -m tests.fixtures.dispatch_stub <card_id> <project_key> <run_id> \
        [--base-url http://localhost:8000]

Exit codes: 0 on success; 1 on any HTTP error; 2 on usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Final

import httpx

DEFAULT_BASE_URL: Final[str] = "http://localhost:8000"
API_PREFIX: Final[str] = "/api/v1/kanban"
REQUEST_TIMEOUT: Final[float] = 30.0


async def run_stub(
    card_id: str,
    project_key: str,
    run_id: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Drive the lifecycle for a single card via the public REST API.

    Returns a small dict summarising what was posted. Raises
    ``httpx.HTTPStatusError`` on any non-2xx response — callers that want
    to suppress a single failure (e.g. soak-mode "best effort" runs)
    should wrap the call in ``try/except`` themselves.
    """
    branch_ref = f"k-e2e-{run_id}"
    summary = (
        f"Stub voltooide dispatch voor {card_id[:8]}; "
        f"fake-merge naar {branch_ref}."
    )

    owns_client = client is None
    http = client or httpx.AsyncClient(
        base_url=base_url, timeout=REQUEST_TIMEOUT
    )
    try:
        deliverable_resp = await http.post(
            f"{API_PREFIX}/cards/{card_id}/deliverables",
            json={"kind": "branch", "ref": branch_ref},
        )
        deliverable_resp.raise_for_status()

        move_resp = await http.post(
            f"{API_PREFIX}/cards/{card_id}/move",
            json={"column": "Done", "summary": summary},
        )
        move_resp.raise_for_status()

        return {
            "card_id": card_id,
            "project_key": project_key,
            "run_id": run_id,
            "branch_ref": branch_ref,
            "summary": summary,
            "final_column": move_resp.json().get("column"),
        }
    finally:
        if owns_client:
            await http.aclose()


async def _async_main(args: argparse.Namespace) -> int:
    try:
        result = await run_stub(
            card_id=args.card_id,
            project_key=args.project_key,
            run_id=args.run_id,
            base_url=args.base_url,
        )
    except httpx.HTTPStatusError as exc:
        print(
            f"stub failed: {exc.request.url} -> {exc.response.status_code} "
            f"{exc.response.text}",
            file=sys.stderr,
        )
        return 1
    except httpx.RequestError as exc:
        print(f"stub transport error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dispatch_stub",
        description=(
            "Deterministic dispatch stub: posts a branch deliverable + "
            "moves the card to Done via the public kanban REST API."
        ),
    )
    parser.add_argument("card_id", help="card id to drive through the lifecycle")
    parser.add_argument(
        "project_key",
        help="project key for the card (used in op-log payloads downstream)",
    )
    parser.add_argument(
        "run_id",
        help="unique run identifier; becomes the branch ref suffix",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("COCKPIT_BASE_URL", DEFAULT_BASE_URL),
        help=f"backend base URL (default: {DEFAULT_BASE_URL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())