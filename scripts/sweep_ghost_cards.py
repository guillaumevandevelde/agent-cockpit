#!/usr/bin/env python3
"""Scan kanban_cards for "ghost cards" — work that's done but not closed.

The board treats a non-terminal card with no active claim the same as one
that's never been started: both look like "still needs dispatching." That
makes two real states invisible:

  - **merged deliverable.** The session shipped work into ``origin/master``
    (branch- or commit-deliverable) and attached it as a deliverable, but
    died before ``move_card(Done)``. Reaper releases the claim, dispatcher
    re-spawns a fresh-context session that immediately re-discovers there's
    nothing to do (kanban card ``2a49492c…``, original observation on card
    ``4e69915f…``).
  - **decomposition done.** An analyst parent decomposed itself into ≥1
    child card and every child carries a ``plan_ref`` back to the parent
    (the analyst's job is over — the executor children carry the actual
    work). Same symptom: the parent lingers in a dispatchable column and
    pays the re-dispatch cost on every reap cycle.

This sweeper surfaces both states as a JSON report. It is **advisory** —
mirrors ``scripts/sweep_dangling_depends_on.py``'s posture ("signal, not
gate"). The decision to close a card is left to a human or a targeted
re-dispatch; the sweeper's contract is to make the state visible.

Output: a single JSON document on stdout (always — no human-readable form)
so the caller can pipe into ``jq``, compare against a saved baseline, or
attach it to a follow-up ``[chore]`` card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "repo_path": "<absolute path>",
      "totals": {
        "non_terminal_cards": <int>,
        "ghost_cards": <int>,
        "by_status": {
          "merged_deliverable": <int>,
          "decomposition_done": <int>
        }
      },
      "rows": [
        {
          "card_id": "...",
          "card_title": "<nullable>",
          "column": "<nullable>",
          "project_key": "<nullable>",
          "status": "merged_deliverable|decomposition_done",
          "evidence": [
            {"kind": "branch"|"commit", "ref": "...", "in_origin_master": true}
          ],
          "children_summary": "<optional — for decomposition_done rows>"
        }
      ]
    }

A healthy card (no merged deliverable AND no decomposed children) is
silently omitted; a Done card is skipped entirely. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, sqlite query failed, or
       git unavailable

Advisory by default — mirrors the other sweepers in this directory.
``--strict`` is for CI/pre-commit: a backlog-cleanup pipeline should
block on a non-zero count rather than admit fresh ghosts back onto the
board.

Usage:
    scripts/sweep_ghost_cards.py [--db PATH] [--repo-path PATH] [--strict]
    scripts/sweep_ghost_cards.py --help
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

DONE_COLUMN = "Done"

STATUS_MERGED_DELIVERABLE = "merged_deliverable"
STATUS_DECOMPOSITION_DONE = "decomposition_done"
ALL_STATUSES = (STATUS_MERGED_DELIVERABLE, STATUS_DECOMPOSITION_DONE)

DEFAULT_DB = "~/.claude-registry/kanban.db"
DEFAULT_REPO_PATH = "."  # resolved to absolute path on use


@dataclass
class GitOracle:
    """Memoized ``git merge-base --is-ancestor`` lookups.

    Without memoization, a sweep over a board with N branch- or
    commit-deliverable rows issues N git invocations. A live board has
    dozens of cards; on a slow shared-machine the cumulative latency is
    noticeable. Cache the (deliverable-id → bool) mapping in-process —
    the SQL pre-fetch already groups by card, so the unique-deliverable
    count is bounded and small.
    """

    repo_path: Path
    cache: dict[str, bool] = field(default_factory=dict)

    def is_ancestor_of_main(self, sha_or_branch: str) -> bool:
        """Return True iff ``sha_or_branch`` is an ancestor of ``main``.

        Uses ``git merge-base --is-ancestor A B`` (exit 0 = A reachable
        from B) so the check works whether the deliverable is a SHA or a
        branch name, and whether or not the branch is still alive in
        ``refs/heads/``/``refs/remotes/`` — both scenarios that bit the
        predecessor card ``4e69915f…``.
        """
        if sha_or_branch in self.cache:
            return self.cache[sha_or_branch]
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_path),
                 "merge-base", "--is-ancestor",
                 sha_or_branch, "origin/master"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            # Bail loudly on infrastructure failures; this is the
            # "git unavailable" exit-2 path the docstring documents.
            raise RuntimeError(
                f"git merge-base failed for {sha_or_branch!r} "
                f"in repo {self.repo_path}: {e}"
            ) from e
        result = proc.returncode == 0
        self.cache[sha_or_branch] = result
        return result


def _resolve_db_path(cli_arg: str | None) -> Path:
    """Resolve the DB path: CLI arg > $KANBAN_DB > default."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_DB")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_DB).expanduser().resolve()


def _resolve_repo_path(cli_arg: str | None) -> Path:
    """Resolve the git repo path: CLI arg > $KANBAN_REPO_PATH > cwd.

    The CLI arg is required by the bash test harness (it points at a
    hermetic fixture), but in ad-hoc operator use the cwd default is
    almost always correct — the operator is inside the repo they want
    to scan.
    """
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_REPO_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    """Return True iff ``name`` is a user table in ``con``.

    A fresh-DB fixture (zero-byte sqlite file) won't have the kanban
    tables yet; the "no tables" path must surface as a clean report
    rather than a sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _is_git_available() -> bool:
    """Return True iff a ``git`` binary is on PATH.

    A machine without git (some minimal CI containers) shouldn't crash
    the sweeper with FileNotFoundError — the no-git path is treated as
    "merged_deliverable cannot be proven" so the sweeper degrades to
    only flagging decomposition_done cards.
    """
    return shutil.which("git") is not None


@dataclass
class CardRow:
    """One non-terminal card pulled from kanban_cards for the sweep."""

    id: str
    title: str | None
    column: str | None
    project_key: str | None
    parent_card_id: str | None


def _fetch_non_terminal_cards(con: sqlite3.Connection) -> list[CardRow]:
    """Return every card whose column != Done, ordered by id for stable tests."""
    if not _table_exists(con, "kanban_cards"):
        return []
    rows = con.execute(
        "SELECT id, title, \"column\", project_key, parent_card_id "
        "FROM kanban_cards WHERE \"column\" != ? "
        "ORDER BY id",
        (DONE_COLUMN,),
    ).fetchall()
    return [
        CardRow(
            id=r[0], title=r[1], column=r[2],
            project_key=r[3], parent_card_id=r[4],
        )
        for r in rows
    ]


def _fetch_children(
    con: sqlite3.Connection, parent_id: str,
) -> list[str]:
    """Return the child card ids of ``parent_id`` (cards whose
    ``parent_card_id`` column equals ``parent_id``).

    The result is the universe of children the decomposition-done
    check reasons over. Order is by id for deterministic tests.
    """
    if not _table_exists(con, "kanban_cards"):
        return []
    rows = con.execute(
        "SELECT id FROM kanban_cards WHERE parent_card_id = ? "
        "ORDER BY id",
        (parent_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _fetch_child_plan_refs(
    con: sqlite3.Connection, child_ids: list[str], parent_id: str,
) -> set[str]:
    """Return the subset of ``child_ids`` that carry a plan_ref deliverable
    back to ``parent_id``.

    The ref JSON has shape ``{"parent_card_id": ..., "plan_deliverable_id": ...}``.
    A child with a plan_ref pointing at a different parent does NOT count
    — the parent's decomposition is only done when every child is wired
    to *this* parent. Malformed plan_refs (unparseable JSON, missing keys)
    are silently treated as "not pointing at this parent" so the
    decomposition-done check is conservative: a card with a flaky child
    ref is not flagged.
    """
    if not child_ids or not _table_exists(con, "kanban_deliverables"):
        return set()
    # One query, not N — build the IN-list dynamically.
    placeholders = ",".join("?" for _ in child_ids)
    rows = con.execute(
        f"SELECT card_id, ref FROM kanban_deliverables "
        f"WHERE kind='plan_ref' AND card_id IN ({placeholders})",
        tuple(child_ids),
    ).fetchall()
    hits: set[str] = set()
    for child_id, raw_ref in rows:
        try:
            ref = json.loads(raw_ref)
        except (TypeError, ValueError):
            continue
        if isinstance(ref, dict) and ref.get("parent_card_id") == parent_id:
            hits.add(child_id)
    return hits


def _check_merged_deliverable(
    con: sqlite3.Connection,
    card_id: str,
    oracle: GitOracle | None,
) -> tuple[bool, list[dict]]:
    """Return (is_ghost, evidence) for the merged-deliverable criterion.

    Iterates the card's ``branch`` and ``commit`` deliverables; for each
    that is an ancestor of ``origin/master`` (per ``GitOracle``), adds a
    record to ``evidence``. The card is a merged-deliverable ghost iff
    ``evidence`` is non-empty.

    When ``oracle`` is None (git unavailable) the function returns
    (False, []) so the merged-deliverable criterion is silently
    skipped; the decomposition_done criterion can still flag the card.
    """
    if oracle is None or not _table_exists(con, "kanban_deliverables"):
        return False, []
    rows = con.execute(
        "SELECT id, kind, ref FROM kanban_deliverables "
        "WHERE card_id = ? AND kind IN ('branch', 'commit') "
        "ORDER BY id",
        (card_id,),
    ).fetchall()
    evidence: list[dict] = []
    for d_id, kind, ref in rows:
        if not ref:
            continue
        if oracle.is_ancestor_of_main(ref):
            evidence.append({
                "deliverable_id": d_id,
                "kind": kind,
                "ref": ref,
                "in_origin_master": True,
            })
    return bool(evidence), evidence


def _check_decomposition_done(
    con: sqlite3.Connection,
    card: CardRow,
) -> tuple[bool, str | None]:
    """Return (is_ghost, children_summary) for the decomposition-done criterion.

    The criterion is: the card has ≥1 child, and every child carries a
    ``plan_ref`` deliverable whose ``parent_card_id`` equals this card's
    id. A parent with zero children does not satisfy the criterion —
    there is no decomposition to be "done" yet — and is silently skipped
    so we don't flag every isolated Backlog card.
    """
    children = _fetch_children(con, card.id)
    if not children:
        return False, None
    children_with_ref = _fetch_child_plan_refs(con, children, card.id)
    if len(children_with_ref) != len(children):
        return False, None
    summary = (
        f"all {len(children)} child card(s) carry a plan_ref back to "
        f"{card.id!r}"
    )
    return True, summary


def sweep(db_path: Path, repo_path: Path) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a
    clean report with 0 ghosts. Raises on file missing/unreadable, on
    schema mismatch inside a query, or on git failure (caller turns
    these into exit-2 errors).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        # Read-only: the sweeper should never mutate the board — the
        # only writes belong to a follow-up chore card / the operator
        # who consumes the report and decides what to close.
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"cannot open kanban DB {db_path}: {e}") from e

    try:
        report: dict = {
            "schema_version": SCHEMA_VERSION,
            "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "db_path": str(db_path),
            "repo_path": str(repo_path),
            "totals": {
                "non_terminal_cards": 0,
                "ghost_cards": 0,
                "by_status": {s: 0 for s in ALL_STATUSES},
            },
            "rows": [],
        }

        cards = _fetch_non_terminal_cards(con)
        report["totals"]["non_terminal_cards"] = len(cards)
        if not cards:
            return report

        oracle: GitOracle | None = None
        if _is_git_available() and repo_path.exists() and repo_path.is_dir():
            oracle = GitOracle(repo_path=repo_path)
        else:
            # Without git (or without a valid repo path), the
            # merged_deliverable criterion can't fire. The
            # decomposition_done criterion still works — it's purely
            # a SQL walk. Surface the degraded mode in stdout so an
            # operator running this against a fresh container notices.
            report["totals"]["git_available"] = False  # type: ignore[assignment]

        for card in cards:
            merged, evidence = _check_merged_deliverable(con, card.id, oracle)
            decomposed, children_summary = _check_decomposition_done(con, card)
            if not merged and not decomposed:
                continue
            # When both criteria fire, prefer the merged-deliverable
            # status (it's the stronger "the work literally shipped"
            # signal). Both checks independently marked the card as
            # ghost, so the row is reported either way.
            status = (
                STATUS_MERGED_DELIVERABLE if merged else STATUS_DECOMPOSITION_DONE
            )
            report["totals"]["by_status"][status] += 1
            report["totals"]["ghost_cards"] += 1
            row = {
                "card_id": card.id,
                "card_title": card.title,
                "column": card.column,
                "project_key": card.project_key,
                "status": status,
                "evidence": evidence,
            }
            if children_summary is not None:
                row["children_summary"] = children_summary
            report["rows"].append(row)
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_ghost_cards.py",
        description=(
            "Find non-Done kanban_cards that look 'done but not closed': "
            "they ship a merged branch/commit deliverable into origin/master, "
            "or they decomposed into ≥1 child card whose every child carries "
            "a plan_ref back to the parent. Emits a JSON report on stdout and "
            "exits 0 (advisory) / 1 (with --strict + hits) / 2 (DB, query, "
            "or git error). Run via scripts/test_sweep_ghost_cards.sh for "
            "the contract."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to kanban.db. Defaults to $KANBAN_DB or "
            f"{DEFAULT_DB}. The bash test harness always passes "
            "a --db-style override via $KANBAN_DB so the real board "
            "is untouched."
        ),
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help=(
            "Path to the git repo whose origin/master is the merge "
            "oracle. Defaults to $KANBAN_REPO_PATH or the current "
            "working directory. The bash test harness points this "
            "at a hermetic fixture repo."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any ghost card is found. Default is advisory: "
            "exit 0 even with hits, mirroring "
            "scripts/sweep_dangling_depends_on.py."
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

    db_path = _resolve_db_path(args.db)
    repo_path = _resolve_repo_path(args.repo_path)
    try:
        report = sweep(db_path, repo_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Set KANBAN_DB=/path/to/kanban.db or pass --db=/path/to/kanban.db.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["ghost_cards"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
