#!/usr/bin/env python3
"""Scan the kanban board for cards whose work is already merged but that never
reached ``Done``.

The failure mode this catches (kanban card ``9c88422a…``, observed on card
``931855b0…``): a session ships — merge to ``origin/master`` lands, the
``branch``/``commit`` deliverable is attached — but the final
``move_card → Done`` never happens (session killed after push, crash, or plain
forgotten). The card keeps its dispatchable column, so auto-dispatch picks it up
again and a **whole duplicate session** is spent re-loading context and
re-verifying work that is already on master (net: 0 new commits). The only
in-band hints — the ``branch`` deliverable and ``dispatch_failures`` — are
visible *after* the context load, i.e. after the cost was already paid.

This sweeper makes that "merged but open" voorraad visible in ≤1 run, without
touching the dispatch flow. It joins the board's non-``Done`` cards against
their ``commit``/``branch`` deliverables and asks git whether each ref is
already contained in the base ref:

- ``commit`` → ``git merge-base --is-ancestor <sha> <base>`` (exact ancestry).
- ``branch`` → ``git cherry <base> <ref>`` with zero ``+`` lines (patch-id
  equivalence, so a squash/rebase-landed branch still counts as merged — the
  same predicate ``scripts/sweep_merged_remote_branches.py`` uses).

A branch ref is resolved through ``refs/heads/<ref>`` → ``refs/remotes/<remote>/<ref>``
→ ``<ref>`` verbatim. The direct-mode ship-recipe deletes the *remote* branch
after a successful push but keeps the local one, so ``refs/heads/`` is usually
the hit for exactly the cards this sweeper is looking for. A ref that resolves
nowhere (branch deleted on both sides, or a deliverable belonging to a different
repo) is reported under ``unresolved_refs`` and never claimed as merged —
advisory means advisory.

Output: a single JSON document on stdout (always — no human-readable form) so
the caller can pipe into ``jq``, compare against a saved baseline, or attach it
to a follow-up [chore] card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "repo_path": "<absolute path>",
      "remote": "origin",
      "base_ref": "origin/master",
      "project_key": "<nullable filter>",
      "dispatchable_only": false,
      "totals": {
        "cards_scanned": <int>,          # non-Done cards with a commit/branch ref
        "cards_merged_but_open": <int>,  # cards with >=1 merged ref  == the hits
        "dispatchable_hits": <int>,      # subset auto-dispatch would re-pick
        "merged_refs": <int>,
        "unresolved_refs": <int>
      },
      "rows": [
        {
          "card_id": "...",
          "card_title": "<nullable>",
          "column": "<nullable>",
          "project_key": "<nullable>",
          "dispatchable": true,
          "merged_refs": [
            {"kind": "branch", "ref": "k-foo-1234",
             "resolved_ref": "refs/heads/k-foo-1234", "unmerged_commits": 0}
          ],
          "unresolved_refs": [{"kind": "branch", "ref": "k-gone-9999"}]
        }
      ]
    }

Cards with no merged ref are silently omitted (an unresolved-only card is not a
hit — there is no evidence its work landed). Exit codes:

    0  clean OR (advisory mode and >=1 hit)
    1  --strict and >=1 hit
    2  usage error, DB/repo missing or unreadable, or a git/sqlite query failed

Advisory by default — mirrors ``scripts/sweep_dangling_depends_on.py`` and
``scripts/sweep_merged_remote_branches.py`` ("signal, not gate"). ``--strict``
is for a board-hygiene pipeline that should block while merged-but-open cards
sit on the board.

``dispatchable`` marks the rows that actually cost a duplicate session: the
dispatcher considers a card iff its column is in ``_DISPATCH_COLUMNS``
(``To Resume``, ``Backlog``) **or** is not one of the fixed ``COLUMNS`` at all
(i.e. an agent lane like ``engineer``/``reviewer``) — see
``_select_dispatch_candidates`` in ``backend/app/kanban/dispatch.py``. The
complement (``intake``, ``Impediment``, ``Awaiting Subtasks``) is non-Done but
never auto-dispatched: ``Awaiting Subtasks`` is deliberate parent-parking that
auto-closes when its children finish, and ``Impediment`` waits on a human. Those
are still reported by default (they are merged-but-open, and a human triaging
the board wants to see them) but flagged ``dispatchable: false``; pass
``--dispatchable-only`` to get just the session-wasting set.

Usage:
    scripts/sweep_merged_but_open_cards.py [--db PATH] [--repo PATH]
        [--remote NAME] [--base-ref REF] [--project-key KEY]
        [--dispatchable-only] [--no-fetch] [--strict]
    scripts/sweep_merged_but_open_cards.py --json    # default; explicit for clarity
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

DONE_COLUMN = "Done"

DEFAULT_DB = "~/.claude-registry/kanban.db"
DEFAULT_REMOTE = "origin"
DEFAULT_BASE_REF = "origin/master"

# Deliverable kinds that name a git object. `pr`/`link`/`note`/`spec`/`plan`
# carry no ref this sweeper can test against the base branch.
GIT_KINDS = ("commit", "branch")

# Columns that are non-Done but never auto-dispatched. Mirrors
# `COLUMNS` minus {"Backlog", "To Resume"} from `backend/app/kanban/schemas.py`
# combined with `_DISPATCH_COLUMNS` in `backend/app/kanban/dispatch.py`: a card
# dispatches iff its column is in `_DISPATCH_COLUMNS` OR is not a fixed column
# at all (agent lanes). Kept as a literal instead of importing the backend so
# the sweeper stays dependency-free like its sibling sweepers.
NON_DISPATCH_COLUMNS = frozenset({"intake", "Impediment", "Awaiting Subtasks", DONE_COLUMN})


class GitError(RuntimeError):
    """A git subcommand failed. Caller turns this into exit 2."""


def _run_git(repo: Path, args: list[str], check: bool = True) -> str:
    """Run ``git -C <repo> <args>`` and return stdout.

    Raises GitError on non-zero exit (when ``check``). ``stderr`` is appended
    to the error message so the operator sees *why* (e.g. "fatal: bad revision"
    when the base ref does not exist locally).
    """
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise GitError(f"git binary not found on PATH: {e}") from e
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
        raise GitError(f"git {' '.join(args)} failed (exit {proc.returncode}): {msg}")
    return proc.stdout


def _git_ok(repo: Path, args: list[str]) -> bool:
    """True iff ``git -C <repo> <args>`` exits 0. Used for boolean probes
    (``rev-parse --verify``, ``merge-base --is-ancestor``) where a non-zero
    exit is the answer, not an error."""
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise GitError(f"git binary not found on PATH: {e}") from e
    return proc.returncode == 0


def _resolve_db_path(cli_arg: str | None) -> Path:
    """Resolve the DB path: CLI arg > $KANBAN_DB > default."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_DB")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_DB).expanduser().resolve()


def _resolve_repo(cli_arg: str | None) -> Path:
    """Resolve the repo path: CLI arg > $SWEEP_REPO > cwd."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("SWEEP_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    """Return True iff ``name`` is a user table in ``con``.

    A fresh-DB fixture (zero-byte sqlite file) won't have the kanban tables
    yet; the "no tables" path must surface as a clean report rather than a
    sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _is_dispatchable(column: str | None) -> bool:
    """True iff auto-dispatch would re-pick a card sitting in ``column``."""
    return (column or "") not in NON_DISPATCH_COLUMNS


def _resolve_ref(repo: Path, remote: str, kind: str, ref: str) -> str | None:
    """Return the git refname/rev that ``ref`` resolves to, or None.

    A ``branch`` deliverable carries a bare branch name. Local first: the
    direct-mode ship-recipe deletes the *remote* branch after a successful
    push but keeps the local one, so ``refs/heads/<ref>`` is the hit for
    exactly the shipped-but-not-Done cards this sweeper hunts. Falls back to
    the remote-tracking ref, then to the ref verbatim (full refname, tag, or
    raw sha). A ``commit`` deliverable is only ever tried verbatim.
    """
    candidates = [ref] if kind == "commit" else [
        f"refs/heads/{ref}",
        f"refs/remotes/{remote}/{ref}",
        ref,
    ]
    for cand in candidates:
        if _git_ok(repo, ["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"]):
            return cand
    return None


def _unmerged_count(repo: Path, base_ref: str, ref: str) -> int:
    """Number of commits in ``ref`` whose patch is not in ``base_ref``.

    ``git cherry <base> <head>`` emits ``+ <sha>`` per commit missing from
    <base> and ``  <sha>`` per commit already there (patch-id equivalence, so
    a squash-landed branch reads as merged). Zero ``+`` lines == fully merged.
    """
    out = _run_git(repo, ["cherry", base_ref, ref])
    return sum(1 for line in out.splitlines() if line.startswith("+"))


def _is_merged(repo: Path, base_ref: str, kind: str, resolved: str) -> tuple[bool, int]:
    """Return ``(merged, unmerged_commits)`` for a resolved ref.

    ``commit`` uses exact ancestry (``merge-base --is-ancestor``) — a card's
    commit deliverable is a single sha, and "is this sha in master" has an
    exact answer. ``branch`` uses ``git cherry`` so a squash/rebase-landed
    branch is still recognised as merged.
    """
    if kind == "commit":
        merged = _git_ok(repo, ["merge-base", "--is-ancestor", resolved, base_ref])
        return merged, 0 if merged else -1
    unmerged = _unmerged_count(repo, base_ref, resolved)
    return unmerged == 0, unmerged


def sweep(
    db_path: Path,
    repo: Path,
    *,
    remote: str = DEFAULT_REMOTE,
    base_ref: str = DEFAULT_BASE_REF,
    project_key: str | None = None,
    dispatchable_only: bool = False,
    do_fetch: bool = True,
) -> dict:
    """Run the sweep and return the report dict.

    Never raises on missing kanban tables or an empty board — those map to a
    clean report. Raises FileNotFoundError / GitError on a missing DB, a
    non-repo ``repo`` path, an unresolvable ``base_ref``, or a failed fetch
    (caller turns those into exit 2).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(f"repo path does not exist: {repo}")
    try:
        _run_git(repo, ["rev-parse", "--git-dir"])
    except GitError as e:
        raise FileNotFoundError(f"repo at {repo} is not a git repository: {e}") from e

    if do_fetch:
        try:
            _run_git(repo, ["fetch", remote])
        except GitError as e:
            raise GitError(
                f"fetching remote {remote!r} failed — is it configured? {e}"
            ) from e

    # A base ref that doesn't resolve would make every card look unmerged —
    # a silent all-clear, which is the worst possible failure for a sweeper.
    # Fail loudly instead.
    if not _git_ok(repo, ["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"]):
        raise GitError(
            f"base ref {base_ref!r} does not resolve in {repo} — "
            "pass --base-ref, or drop --no-fetch so the remote-tracking ref exists"
        )

    try:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"cannot open kanban DB {db_path}: {e}") from e

    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "repo_path": str(repo),
        "remote": remote,
        "base_ref": base_ref,
        "project_key": project_key,
        "dispatchable_only": dispatchable_only,
        "totals": {
            "cards_scanned": 0,
            "cards_merged_but_open": 0,
            "dispatchable_hits": 0,
            "merged_refs": 0,
            "unresolved_refs": 0,
        },
        "rows": [],
    }

    try:
        if not _table_exists(con, "kanban_cards") or not _table_exists(
            con, "kanban_deliverables"
        ):
            return report

        placeholders = ",".join("?" for _ in GIT_KINDS)
        params: list[str] = [DONE_COLUMN, *GIT_KINDS]
        sql = (
            'SELECT c.id, c.title, c."column", c.project_key, d.kind, d.ref '
            "FROM kanban_cards c "
            "JOIN kanban_deliverables d ON d.card_id = c.id "
            'WHERE c."column" != ? AND d.kind IN (' + placeholders + ") "
        )
        if project_key:
            sql += "AND c.project_key = ? "
            params.append(project_key)
        sql += "ORDER BY c.id, d.kind, d.ref"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    # Group by card so one row per card carries all of its refs.
    cards: dict[str, dict] = {}
    for card_id, title, column, pkey, kind, ref in rows:
        entry = cards.setdefault(
            card_id,
            {
                "card_id": card_id,
                "card_title": title,
                "column": column,
                "project_key": pkey,
                "dispatchable": _is_dispatchable(column),
                "merged_refs": [],
                "unresolved_refs": [],
            },
        )
        entry.setdefault("_refs", []).append((kind, (ref or "").strip()))

    # Cache per (kind, ref) so a ref attached to several cards costs one probe.
    verdicts: dict[tuple[str, str], tuple[str | None, bool, int]] = {}

    for entry in cards.values():
        if dispatchable_only and not entry["dispatchable"]:
            entry["_skip"] = True
            continue
        report["totals"]["cards_scanned"] += 1
        for kind, ref in entry.pop("_refs"):
            if not ref:
                continue
            key = (kind, ref)
            if key not in verdicts:
                resolved = _resolve_ref(repo, remote, kind, ref)
                if resolved is None:
                    verdicts[key] = (None, False, -1)
                else:
                    merged, unmerged = _is_merged(repo, base_ref, kind, resolved)
                    verdicts[key] = (resolved, merged, unmerged)
            resolved, merged, unmerged = verdicts[key]
            if resolved is None:
                entry["unresolved_refs"].append({"kind": kind, "ref": ref})
                report["totals"]["unresolved_refs"] += 1
            elif merged:
                entry["merged_refs"].append({
                    "kind": kind,
                    "ref": ref,
                    "resolved_ref": resolved,
                    "unmerged_commits": 0,
                })
                report["totals"]["merged_refs"] += 1

    for entry in cards.values():
        if entry.pop("_skip", False):
            continue
        entry.pop("_refs", None)
        if not entry["merged_refs"]:
            continue
        report["totals"]["cards_merged_but_open"] += 1
        if entry["dispatchable"]:
            report["totals"]["dispatchable_hits"] += 1
        report["rows"].append(entry)

    report["rows"].sort(key=lambda r: (not r["dispatchable"], r["card_id"]))
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_merged_but_open_cards.py",
        description=(
            "Find non-Done kanban cards whose commit/branch deliverable is "
            "already merged into the base ref (default origin/master) — work "
            "that landed but never reached Done, so auto-dispatch keeps "
            "re-picking the card and burns a duplicate session. Emits a JSON "
            "report on stdout and exits 0 (advisory) / 1 (with --strict + "
            "hits) / 2 (DB, repo, or query error). Run via "
            "scripts/test_sweep_merged_but_open_cards.sh for the contract."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to kanban.db. Defaults to $KANBAN_DB or "
            f"{DEFAULT_DB}. The bash test harness always passes a --db "
            "override via the env var so the real board is untouched."
        ),
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "Path to the git working tree holding the cards' branches. "
            "Defaults to $SWEEP_REPO or the current working directory."
        ),
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help=(
            f"Remote name used to resolve a branch deliverable via "
            f"refs/remotes/<remote>/<ref>. Defaults to {DEFAULT_REMOTE!r}."
        ),
    )
    parser.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help=(
            f"Ref the deliverables are tested against. Defaults to "
            f"{DEFAULT_BASE_REF!r}. A base ref that does not resolve is a "
            "hard error (exit 2), never a silent all-clear."
        ),
    )
    parser.add_argument(
        "--project-key",
        default=None,
        help=(
            "Only scan cards with this project_key. Useful on a multi-project "
            "board: another project's branches don't exist in this repo and "
            "would land in unresolved_refs as noise."
        ),
    )
    parser.add_argument(
        "--dispatchable-only",
        action="store_true",
        help=(
            "Only report cards auto-dispatch would re-pick (column in "
            "To Resume/Backlog or an agent lane). Drops intake, Impediment "
            "and Awaiting Subtasks — non-Done but never auto-dispatched."
        ),
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help=(
            "Skip `git fetch <remote>` and use whatever is already in "
            "refs/remotes/<remote>/*. Pure local mode for tests, CI, and "
            "offline use; without this flag the sweeper refreshes first so a "
            "just-merged card is recognised on the next run."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any merged-but-open card is found. Default is "
            "advisory: exit 0 even with hits, mirroring the sibling sweepers "
            "(sweep_dangling_depends_on.py etc.)."
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
    repo = _resolve_repo(args.repo)
    try:
        report = sweep(
            db_path,
            repo,
            remote=args.remote,
            base_ref=args.base_ref,
            project_key=args.project_key,
            dispatchable_only=args.dispatchable_only,
            do_fetch=not args.no_fetch,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Set KANBAN_DB=/path/to/kanban.db (or --db) and --repo=/path/to/repo.",
            file=sys.stderr,
        )
        return 2
    except (GitError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["cards_merged_but_open"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
