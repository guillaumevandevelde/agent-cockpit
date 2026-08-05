#!/usr/bin/env python3
"""Scan the new-app interview scratch directory for stale leftovers.

The cardless inceptie route (kanban card from
``docs/cockpit/kaartloze-app-inceptie-decision.md`` §5) writes each
running interview to ``~/.claude-registry/interviews/<slug>/`` so a
crashed session or a reaped worktree can be picked up with
``/new-app --resume <slug>``. The directory is intentionally durable
— a failed birth must NOT silently delete the design+plan — but that
durability creates a new silent accumulation: an abandoned interview
that the operator forgot to clean up, or a ``born``-phase dir whose
step 6 ``mv .trash/`` was lost to a crash.

This is the vangnet for that accumulation. The sweeper reads each
scratch dir's ``state.json`` and flags two classes:

  1. **Born leftovers** — ``phase == "born"`` at any age. The new-app
     skill guarantees a ``born``-phase dir is supposed to be moved
     into ``.trash/`` (a dot-prefixed dir the sweeper skips). A
     ``born``-phase dir still sitting under ``interviews/`` is the
     exact "step 6 was lost" failure the sweep exists to surface.
  2. **Stale flights** — any other phase (or unreadable state.json)
     whose ``mtime`` is older than ``--older-than-days`` (default 7).
     The phase tells the operator whether they can resume (``interview``
     / ``ready_for_birth``) or whether the work is already shipped
     (``born``).

Output: a single JSON document on stdout (always — no human-readable
form) so the caller can pipe into ``jq``, diff against a saved
baseline, or attach the report to a follow-up ``[chore]`` card.
Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "interviews_dir": "<absolute path>",
      "older_than_days": <int>,
      "totals": {
        "interviews_scanned": <int>,   # non-dot directories considered
        "flagged": <int>               # rows produced
      },
      "rows": [
        {
          "slug": "<dir name>",
          "path": "<absolute path>",
          "age_days": <int>,           # whole days, age since mtime
          "phase": "interview|ready_for_birth|born|unknown",
          "reason": "born-phase leftover" | "older than <N> days",
          "resume_cmd": "<literal command>",
          "state_error": "<optional — populated when state.json unreadable>"
        }
      ]
    }

Healthy dirs (young interview / ready_for_birth, or any dot-prefixed
dir) are silently omitted. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, --interviews-dir missing/unreadable

Advisory by default — mirrors the sibling sweepers
(``scripts/sweep_merged_remote_branches.py``, etc.). ``--strict``
is for CI: a cleanup pipeline should block on a non-zero count
rather than let forgotten interviews pile up.

The script does NOT delete anything. ``rm`` is deny-listed repo-wide
(``CLAUDE.md`` ``Bash(rm:*)`` deny), and the new-app skill itself
prescribes ``mv`` into ``.trash/`` for any removal. The human (or a
follow-up chore card) picks between resume and delete.

Usage:
    scripts/sweep_stale_interviews.py [--interviews-dir PATH]
        [--older-than-days N] [--strict] [--help]
    scripts/sweep_stale_interviews.py --json     # default; explicit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

# Default scratch directory for the new-app interview flow. Anything
# outside this location is not a candidate for the sweep — the operator
# can override with --interviews-dir or $INTERVIEWS_DIR for tests and
# sandboxed setups.
DEFAULT_INTERVIEWS_DIR = "~/.claude-registry/interviews"

# ``new-app``'s copy-then-delete step (``.claude/skills/new-app/SKILL.md``
# §6) moves the scratch dir into ``.trash/<slug>-<timestamp>`` after a
# successful birth. ``.trash/`` is dot-prefixed so ``--resume`` and
# this sweeper skip it — the retention story for ``.trash/`` is a
# separate cleanup task and is NOT this script's concern.
#
# The general "skip any dot-prefixed dir" rule catches ``.trash/``,
# ``.cache/``, ``.partial/`` and any future internal store the skill
# might add. A scratch dir that wants to be visible MUST live at
# ``interviews/<slug>`` with a non-dot slug.
SKIP_DOT_PREFIX = "."

# Phase values from ``state.json`` (``.claude/skills/new-app/SKILL.md``
# "state.json"). Anything outside this set is treated as ``"unknown"``
# — the sweep is read-only, so a typo in the new-app skill would
# surface as unknown + the age check, not as a silent skip.
KNOWN_PHASES = frozenset({"interview", "ready_for_birth", "born"})

# Resume command shape. The new-app skill accepts the same literal command for
# every phase. For ``born``, resume verifies the successful birth and performs
# the final ``mv`` into ``.trash/``; it must NOT attempt a second birth.
RESUME_CMD = "/new-app --resume {slug}"


def _resolve_interviews_dir(cli_arg: str | None) -> Path:
    """Resolve the interviews dir: CLI arg > $INTERVIEWS_DIR > default.

    Returns an absolute, resolved path so JSON output is stable across
    invocations from different cwds (and so a misuse from /tmp surfaces
    clearly in the report rather than as a bare ``.``).
    """
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("INTERVIEWS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_INTERVIEWS_DIR).expanduser().resolve()


def _safe_age_days(path: Path, now: datetime) -> int:
    """Return the whole-day age of ``path`` relative to ``now``.

    ``path.stat().st_mtime`` is the dir-mtime the operator last touched
    (the new-app skill rewrites ``state.json`` after every approved
    section, so mtime is the closest available proxy for "last
    activity"). The result is floored to completed whole days, so a
    directory is flagged only when its age is strictly greater than the
    threshold.
    """
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    delta = now - mtime
    # ``total_seconds()`` is non-negative for any path under the
    # default ``interviews/`` location; clamp to 0 defensively so a
    # future-dated mtime (clock skew) never produces a negative age.
    # Floor (whole days completed) — the natural reading of "age
    # in days" matches the threshold's "older than N days" idiom: a
    # 7-day-old dir is NOT yet older than the 7-day threshold, an
    # 8-day-old dir is.
    seconds = max(0.0, delta.total_seconds())
    return int(seconds // 86400)


def _read_state(state_path: Path) -> tuple[str | None, str | None]:
    """Return ``(phase, error)`` from ``state.json``, or ``(None, err)``.

    Missing file or unreadable JSON is reported as ``phase=None`` plus
    a stringified error, so the caller can still surface the dir as a
    row with ``phase="unknown"`` and ``state_error`` populated. There
    is no situation where the sweep should silently skip — every
    non-dot directory is a candidate, and the row lets the operator
    see WHY it was flagged when the reason is "I can't read state.json".
    """
    if not state_path.is_file():
        return None, "no state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, f"unreadable state.json: {e}"
    phase = data.get("phase")
    if not isinstance(phase, str) or phase not in KNOWN_PHASES:
        return None, f"unknown phase: {phase!r}"
    return phase, None


def _row_for(scratch_dir: Path, interviews_dir: Path, now: datetime,
             threshold_days: int) -> dict | None:
    """Return a report row for ``scratch_dir``, or None to skip.

    The skip rules match the kanban acceptance criteria:

      - phase == "born" → ALWAYS produce a row (the sweeper exists for
        this case; the new-app skill's step 6 is supposed to have moved
        the dir into ``.trash/`` already).
      - phase unknown / state.json missing / state.json corrupt →
        treat as "unknown"; flagged when age > threshold (we can't
        prove the dir is in flight, so the age guard is the only
        honest signal).
      - phase in {interview, ready_for_birth} → flagged when age >
        threshold (a normal in-flight interview that the operator
        forgot).

    ``reason`` is a human-readable one-line summary that explains why
    the row was produced (so the follow-up chore card can paste it
    into the activity feed without re-deriving the logic).
    """
    slug = scratch_dir.name
    age_days = _safe_age_days(scratch_dir, now)
    phase, state_error = _read_state(scratch_dir / "state.json")
    effective_phase = phase if phase is not None else "unknown"

    if effective_phase == "born":
        reason = "born-phase leftover (should have been moved to .trash/ by new-app step 6)"
    elif age_days > threshold_days:
        reason = f"older than {threshold_days} days"
    else:
        return None

    resume_cmd = RESUME_CMD.format(slug=slug)

    row = {
        "slug": slug,
        "path": str(scratch_dir),
        "age_days": age_days,
        "phase": effective_phase,
        "reason": reason,
        "resume_cmd": resume_cmd,
    }
    if state_error:
        row["state_error"] = state_error
    return row


def sweep(interviews_dir: Path, threshold_days: int, now: datetime | None = None) -> dict:
    """Run the sweep against ``interviews_dir`` and return the report dict.

    Never raises on an empty directory or an unreadable state.json —
    those surface as ``rows=[]`` and rows-with-``state_error``,
    respectively. Raises on the directory missing (the operator passed
    a bad path, which is a usage error → exit 2).
    """
    if not interviews_dir.exists() or not interviews_dir.is_dir():
        raise FileNotFoundError(f"interviews dir does not exist: {interviews_dir}")

    sweep_now = now if now is not None else datetime.now(UTC)
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": sweep_now.isoformat(timespec="seconds"),
        "interviews_dir": str(interviews_dir),
        "older_than_days": threshold_days,
        "totals": {
            "interviews_scanned": 0,
            "flagged": 0,
        },
        "rows": [],
    }

    for entry in sorted(interviews_dir.iterdir()):
        # Skip non-directories (e.g. a stray file the new-app skill
        # accidentally dropped) and dot-prefixed entries (the .trash
        # internal store, .cache, .anything). The dot rule also
        # rejects ".." / "."-rebinds — though iterdir() never
        # returns those, defense-in-depth is cheap here.
        if not entry.is_dir():
            continue
        if entry.name.startswith(SKIP_DOT_PREFIX):
            continue
        report["totals"]["interviews_scanned"] += 1
        row = _row_for(entry, interviews_dir, sweep_now, threshold_days)
        if row is not None:
            report["totals"]["flagged"] += 1
            report["rows"].append(row)

    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_stale_interviews.py",
        description=(
            "Find new-app interview scratch directories that are stale "
            "(older than --older-than-days) or that were never moved to "
            ".trash/ after a successful birth (phase == 'born'). Emits a "
            "JSON report on stdout and exits 0 (advisory) / 1 (with "
            "--strict + hits) / 2 (dir missing). Run via "
            "scripts/test_sweep_stale_interviews.sh for the contract."
        ),
    )
    parser.add_argument(
        "--interviews-dir",
        default=None,
        help=(
            "Path to the interviews directory. Defaults to "
            f"$INTERVIEWS_DIR or {DEFAULT_INTERVIEWS_DIR}. The bash "
            "test harness always passes --interviews-dir so the live "
            "interviews/ is untouched."
        ),
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=7,
        help=(
            "Minimum age in days for an interview (other than 'born' "
            "phase) to be flagged. Default 7. Respects the 'new-app' "
            "skill's claim that a stale interview is one the operator "
            "forgot to clean up; a 7-day cutoff gives the human a "
            "monkey-pause window without letting forgotten dirs "
            "accumulate indefinitely."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any stale interview is found. Default is "
            "advisory: exit 0 even with hits, mirroring the sibling "
            "sweepers (scripts/sweep_merged_remote_branches.py etc.)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (default; flag exists for pipeline clarity).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.older_than_days < 0:
        print(
            f"ERROR: --older-than-days must be >= 0, got {args.older_than_days}",
            file=sys.stderr,
        )
        return 2

    interviews_dir = _resolve_interviews_dir(args.interviews_dir)
    try:
        report = sweep(interviews_dir, threshold_days=args.older_than_days)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Pass --interviews-dir=/path/to/interviews or set "
            "INTERVIEWS_DIR=/path/to/interviews.",
            file=sys.stderr,
        )
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["flagged"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
