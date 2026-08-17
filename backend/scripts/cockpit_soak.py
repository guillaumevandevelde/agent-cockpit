"""Nightly soak harness voor de dispatch lifecycle (kaart 6b662c35…).

Spawnt periodiek fake kanban-kaarten tegen een scratch-sandbox backend en
draait ze via :mod:`tests.fixtures.dispatch_stub` door de hele lifecycle.
Per sessie wordt één jsonl-regel naar ``logs/soak/<UTC-date>.jsonl``
geschreven; ``scripts/cockpit-soak-report.py`` aggregeert die 's avonds
tot ``logs/soak/<date>.json``.

Achtergrond + recept: ``docs/cockpit/e2e-soak-harness-design.md`` §4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx

# Maak ``backend/`` importbaar zodat ``tests.fixtures.dispatch_stub`` werkt
# ongeacht de werkdirectory van waaruit het script wordt aangeroepen.
_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from tests.fixtures.dispatch_stub import run_stub  # noqa: E402

DEFAULT_BASE_URL: Final[str] = "http://localhost:8000"
DEFAULT_PROJECT_KEY: Final[str] = "cockpit-soak"
DEFAULT_PROVIDER: Final[str] = "minimax"  # canonieke provider per §5 van het design
DEFAULT_INTERVAL_S: Final[int] = 15 * 60  # M = 15 min — geeft 32+ runs in 8 uur
DEFAULT_DURATION_S: Final[int] = 8 * 3600
DEFAULT_LOG_DIR: Final[Path] = Path("logs/soak")
API_PREFIX: Final[str] = "/api/v1/kanban"
REQUEST_TIMEOUT: Final[float] = 30.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_date_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


async def _create_card(
    http: httpx.AsyncClient,
    *,
    project_key: str,
    title: str,
    description: str,
    confirm_new_project: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_key": project_key,
        "title": title,
        "description": description,
        "column": "Backlog",
        "work_type": "feature",
    }
    if confirm_new_project:
        payload["confirm_new_project"] = True
    resp = await http.post(f"{API_PREFIX}/cards", json=payload)
    resp.raise_for_status()
    return resp.json()


async def _get_card(http: httpx.AsyncClient, card_id: str) -> dict[str, Any]:
    resp = await http.get(f"{API_PREFIX}/cards/{card_id}")
    resp.raise_for_status()
    return resp.json()


async def _healthcheck(http: httpx.AsyncClient) -> None:
    """Verify the backend is reachable. Raises on any non-2xx response."""
    resp = await http.get("/health", timeout=5.0)
    resp.raise_for_status()


async def _one_session(
    *,
    http: httpx.AsyncClient,
    project_key: str,
    run_id: str,
    jsonl_fp,
    confirm_new_project: bool,
) -> dict[str, Any]:
    """Spawn one fake card, run the stub, log the per-session outcome."""
    started_at = time.monotonic()
    wall_start = time.time()
    title = f"[soak] lifecycle run {run_id}"
    description = (
        f"Geautomatiseerde soak-kaart (run={run_id}). Stub drijft de "
        f"lifecycle; geen echte provider betrokken."
    )

    outcome: dict[str, Any] = {
        "run_id": run_id,
        "project_key": project_key,
        "started_at": _utc_iso(wall_start),
        "card_id": None,
        "claim_at": None,
        "move_to_done_at": None,
        "exit_reason": None,
        "deliverable_kinds": [],
        "worktree_path": None,
        "duration_ms": None,
        "error": None,
    }

    try:
        card = await _create_card(
            http,
            project_key=project_key,
            title=title,
            description=description,
            confirm_new_project=confirm_new_project,
        )
        card_id = card["id"]
        outcome["card_id"] = card_id

        # Wait up to 30s for the dispatch tick to claim the card (Doing).
        deadline = time.monotonic() + 30.0
        claimed_at: float | None = None
        while time.monotonic() < deadline:
            current = await _get_card(http, card_id)
            if current.get("column") in ("Doing", "Review", "Done"):
                claimed_at = time.time()
                outcome["claim_at"] = _utc_iso(claimed_at)
                break
            await asyncio.sleep(1.0)
        if claimed_at is None:
            outcome["exit_reason"] = "claim_timeout"
            return outcome

        # Run the stub: attach branch deliverable + move to Done.
        stub = await run_stub(
            card_id=card_id,
            project_key=project_key,
            run_id=run_id,
            client=http,
        )
        outcome["move_to_done_at"] = _utc_iso(time.time())
        outcome["deliverable_kinds"] = ["branch"]
        outcome["exit_reason"] = "Done"
        outcome["worktree_path"] = (
            f"/tmp/cockpit-e2e-sandbox/{run_id}/.claude/worktrees/"
            f"k-e2e-{run_id}"
        )

        _ = stub  # consumed for its side effects only
    except httpx.HTTPStatusError as exc:
        outcome["exit_reason"] = "http_error"
        outcome["error"] = (
            f"{exc.request.url} -> {exc.response.status_code} "
            f"{exc.response.text[:200]}"
        )
    except httpx.RequestError as exc:
        outcome["exit_reason"] = "transport_error"
        outcome["error"] = str(exc)
    except Exception as exc:  # pragma: no cover — vangnet
        outcome["exit_reason"] = "exception"
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        outcome["duration_ms"] = int((time.monotonic() - started_at) * 1000)

    jsonl_fp.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    jsonl_fp.flush()
    return outcome


async def _run(args: argparse.Namespace) -> int:
    log_dir: Path = args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"{_utc_date_slug()}.jsonl"

    end_at = time.monotonic() + args.duration_s
    run_idx = 0

    print(
        f"[soak] start base_url={args.base_url} project={args.project_key} "
        f"duration={args.duration_s}s interval={args.interval_s}s "
        f"log={jsonl_path}",
        file=sys.stderr,
        flush=True,
    )

    confirm_new_project = True  # eerste sessie kan het project bootstrappen

    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=REQUEST_TIMEOUT
    ) as http:
        await _healthcheck(http)
        with jsonl_path.open("a", encoding="utf-8") as jsonl_fp:
            while time.monotonic() < end_at:
                run_id = f"{_utc_date_slug()}-{run_idx:04d}-{int(time.time())}"
                outcome = await _one_session(
                    http=http,
                    project_key=args.project_key,
                    run_id=run_id,
                    jsonl_fp=jsonl_fp,
                    confirm_new_project=confirm_new_project,
                )
                confirm_new_project = False  # daarna is het project bekend
                run_idx += 1
                print(
                    f"[soak] {run_id} -> {outcome['exit_reason']} "
                    f"({outcome['duration_ms']}ms)",
                    file=sys.stderr,
                    flush=True,
                )
                remaining = end_at - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(args.interval_s, remaining))

    print(f"[soak] done — {run_idx} sessions logged to {jsonl_path}", file=sys.stderr)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cockpit_soak",
        description=(
            "Nightly soak harness: spawn fake kanban-kaarten en drijf ze "
            "via dispatch_stub door de lifecycle. Per-sessie jsonl-regel "
            "in logs/soak/<UTC-date>.jsonl."
        ),
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("COCKPIT_BASE_URL", DEFAULT_BASE_URL),
        help=f"backend base URL (default: {DEFAULT_BASE_URL})",
    )
    p.add_argument(
        "--project-key",
        default=DEFAULT_PROJECT_KEY,
        help=(
            "kanban project key waaronder soak-kaarten landen "
            f"(default: {DEFAULT_PROJECT_KEY})"
        ),
    )
    p.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=(
            "canonieke provider pinning voor dispatch — enkel als "
            "metadata gelogd, niet door dit script zelf gebruikt "
            f"(default: {DEFAULT_PROVIDER})"
        ),
    )
    p.add_argument(
        "--interval-s",
        type=int,
        default=DEFAULT_INTERVAL_S,
        help=f"seconden tussen sessies (default: {DEFAULT_INTERVAL_S})",
    )
    p.add_argument(
        "--duration-s",
        type=int,
        default=DEFAULT_DURATION_S,
        help=f"totale duur in seconden (default: {DEFAULT_DURATION_S})",
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"directory voor jsonl (default: {DEFAULT_LOG_DIR})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())