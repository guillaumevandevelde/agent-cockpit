"""Aggregate ``logs/soak/<UTC-date>.jsonl`` → ``logs/soak/<date>.json``.

Draait na elke soak-night om de per-sessie jsonl-regels samen te trekken
tot één aggregate-bestand met p50/p95 doorlooptijd, exit_reason-distributie
en fail-rate-per-scenario. Het aggregate-bestand is wat het cockpit-dashboard
en de alert-scripts lezen; de jsonl is de canonieke bron.

Recept: ``docs/cockpit/e2e-soak-harness-design.md`` §4.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Final

DEFAULT_LOG_DIR: Final[Path] = Path("logs/soak")


def _percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return int(round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac))


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(
        int(r["duration_ms"]) for r in rows if isinstance(r.get("duration_ms"), int)
    )
    exit_counter: Counter[str] = Counter(
        str(r.get("exit_reason") or "unknown") for r in rows
    )
    failures = sum(
        1
        for r in rows
        if str(r.get("exit_reason") or "") not in ("Done",)
    )
    total = len(rows)
    return {
        "sessions_total": total,
        "sessions_done": exit_counter.get("Done", 0),
        "sessions_failed": failures,
        "fail_rate": (failures / total) if total else 0.0,
        "duration_ms": {
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "max": durations[-1] if durations else 0,
        },
        "exit_reason_distribution": dict(exit_counter),
        "scenario_failures": [
            {
                "run_id": r.get("run_id"),
                "exit_reason": r.get("exit_reason"),
                "error": r.get("error"),
            }
            for r in rows
            if str(r.get("exit_reason") or "") != "Done"
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cockpit-soak-report",
        description=(
            "Aggregate logs/soak/<UTC-date>.jsonl naar logs/soak/<date>.json "
            "met p50/p95 doorlooptijd en exit_reason-distributie."
        ),
    )
    p.add_argument(
        "--date",
        help="UTC-datum (YYYY-MM-DD); default = laatste jsonl in --log-dir",
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"soak-log directory (default: {DEFAULT_LOG_DIR})",
    )
    return p


def _resolve_date(args: argparse.Namespace) -> str:
    if args.date:
        return args.date
    candidates = sorted(args.log_dir.glob("*.jsonl"))
    if not candidates:
        raise SystemExit(
            f"geen jsonl gevonden in {args.log_dir}; geef --date YYYY-MM-DD mee"
        )
    return candidates[-1].stem


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    date_slug = _resolve_date(args)
    jsonl_path = args.log_dir / f"{date_slug}.jsonl"
    json_out_path = args.log_dir / f"{date_slug}.json"

    if not jsonl_path.exists():
        print(f"ERROR: jsonl niet gevonden: {jsonl_path}", file=sys.stderr)
        return 1

    rows: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"WARN: sla jsonl-regel over (parse error: {exc}): {line[:120]}",
                    file=sys.stderr,
                )

    aggregate = _aggregate(rows)
    aggregate["date"] = date_slug
    aggregate["source"] = str(jsonl_path)

    json_out_path.write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {json_out_path}: {aggregate['sessions_total']} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())