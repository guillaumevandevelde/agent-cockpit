#!/usr/bin/env python3
"""Scan remote-tracking branches for ones fully merged into the base branch.

The direct-mode ship-recipe deletes a branch on ``origin`` after a successful
push to master (kanban card ``3027671c…`` — that card fixed the *new* leak).
This sweeper is the vangnet for the *historical* voorraad and for branches
that escape the recipe (PR-route branches that never merged, manual pushes,
sessions that crashed after push but before ship). A branch is "fully merged"
when ``git cherry <base> <remote>/<branch>`` emits zero ``+`` lines — every
commit's patch is already in <base>, so the branch is dead on the remote.

Output: a single JSON document on stdout (always — no human-readable form) so
the caller can pipe into ``jq``, compare against a saved baseline, or attach
it to a follow-up [chore] card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "repo_path": "<absolute>",
      "remote": "origin",
      "base_branch": "master",
      "excluded": ["master", ...],            # base + any --exclude flags
      "totals": {
        "branches_scanned": <int>,             # remote refs considered
        "fully_merged": <int>                  # 0 unmerged == dead branch
      },
      "rows": [
        {
          "branch": "<name>",                  # refs/remotes/<remote>/<branch>
          "remote_ref": "origin/<branch>",
          "unmerged_commits": 0
        }
      ]
    }

Branches that are not fully merged (unmerged_commits > 0) are silently omitted
— the sweeper is a dead-branch vangnet, not a general "what's in flight?"
report. Exit codes:

    0  clean OR (advisory mode and >=1 hit)
    1  --strict and >=1 hit
    2  usage error, repo missing/unreadable, fetch failed, or git query error

Advisory by default — mirrors ``scripts/sweep_dangling_depends_on.py``'s
posture ("signal, not gate"). ``--strict`` is for CI: a branch-cleanup
pipeline should block on a non-zero count rather than let dead branches pile
up on ``origin``.

Usage:
    scripts/sweep_merged_remote_branches.py [--repo PATH] [--remote NAME]
        [--base-branch NAME] [--exclude BRANCH]... [--no-fetch] [--strict]
    scripts/sweep_merged_remote_branches.py --json    # default; explicit for clarity
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

DEFAULT_REMOTE = "origin"
DEFAULT_BASE_BRANCH = "master"

# Refs namespace under ``refs/remotes/<remote>/`` — git for-each-ref returns
# the full refname; we strip the prefix to get the branch name.
REMOTE_REFS_PREFIX_FMT = "refs/remotes/{remote}/"


class GitError(RuntimeError):
    """A git subcommand failed. Caller turns this into exit 2."""


def _run_git(repo: Path, args: list[str], check: bool = True) -> str:
    """Run ``git -C <repo> <args>`` and return stdout.

    Raises GitError on non-zero exit (when ``check``). ``stderr`` is appended
    to the error message so the operator can see *why* (e.g. "fatal: 'origin'
    does not appear to be a git repository" when the remote is misconfigured).
    """
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as e:
        raise GitError(f"git binary not found on PATH: {e}") from e
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or "(no output)"
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {msg}"
        )
    return proc.stdout


def _resolve_repo(cli_arg: str | None) -> Path:
    """Resolve the repo path: CLI arg > $SWEEP_REPO > cwd.

    Always returns an absolute, resolved path so JSON output is stable across
    invocations from different cwds (and so a misuse from /tmp surfaces
    clearly in the report rather than as a bare ``.``).
    """
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("SWEEP_REPO")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _list_remote_branches(repo: Path, remote: str) -> list[str]:
    """Return short branch names under ``refs/remotes/<remote>/``.

    ``git for-each-ref`` is preferred over ``git branch -r`` because the
    former has a stable, parseable format (``%(refname:short)`` is one ref
    per line) and doesn't emit the leading ``  origin/HEAD ->`` decoration
    some Git versions add. Strips the ``<remote>/`` prefix so callers see
    ``feat-foo`` rather than ``origin/feat-foo``.
    """
    prefix = REMOTE_REFS_PREFIX_FMT.format(remote=remote)
    out = _run_git(
        repo,
        ["for-each-ref", "--format=%(refname:short)", prefix],
    )
    branches: list[str] = []
    plen = len(remote) + 1  # "origin/"
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith(remote + "/"):
            # Defensive: for-each-ref should always prefix, but skip anything
            # weird rather than emit a bogus branch name.
            continue
        branches.append(line[plen:])
    return branches


def _unmerged_count(repo: Path, base: str, remote_ref: str) -> int:
    """Return the number of commits in ``remote_ref`` not present in ``base``.

    ``git cherry <base> <ref>`` emits one line per commit in <ref>: ``+ sha``
    when the commit is NOT in <base>, ``  sha`` when it is. ``HEAD~`` of a
    FF-merged branch shows empty output (every commit was in <base>); a live
    branch shows one ``+`` line per ahead-of-base commit. The count of ``+``
    lines is the "unmerged_commits" total reported in the row.
    """
    out = _run_git(repo, ["cherry", base, remote_ref])
    return sum(1 for line in out.splitlines() if line.startswith("+"))


def sweep(
    repo: Path,
    remote: str,
    base_branch: str,
    excluded: set[str],
    *,
    do_fetch: bool = True,
) -> dict:
    """Run the sweep against ``repo`` and return the report dict.

    ``excluded`` is the set of branch names to skip (always contains
    ``base_branch``; caller may add more). When ``do_fetch`` is True, runs
    ``git fetch <remote>`` first so the local ``refs/remotes/<remote>/*``
    cache reflects the remote's current state; on fetch failure, raises
    GitError (caller maps to exit 2). When False, enumerates whatever is in
    the cache — pure local mode for tests / CI / offline use.
    """
    if not repo.exists() or not repo.is_dir():
        raise FileNotFoundError(f"repo path does not exist: {repo}")
    # Cheap sanity check: ``git rev-parse --git-dir`` confirms it's a git
    # working tree before we try any subcommand. Without this, the first
    # for-each-ref would raise GitError("not a git repository") with a
    # less-actionable message.
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

    branches = _list_remote_branches(repo, remote)
    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repo_path": str(repo),
        "remote": remote,
        "base_branch": base_branch,
        "excluded": sorted(excluded),
        "totals": {
            "branches_scanned": 0,
            "fully_merged": 0,
        },
        "rows": [],
    }

    for branch in sorted(branches):
        report["totals"]["branches_scanned"] += 1
        if branch in excluded:
            continue
        try:
            unmerged = _unmerged_count(repo, base_branch, f"{remote}/{branch}")
        except GitError as e:
            # One bad ref shouldn't abort the whole sweep — the operator can
            # see which one failed from stderr; the rest of the report is
            # still useful. Surface as a row with the error captured.
            report["rows"].append({
                "branch": branch,
                "remote_ref": f"{remote}/{branch}",
                "unmerged_commits": -1,
                "error": str(e),
            })
            continue
        if unmerged == 0:
            report["totals"]["fully_merged"] += 1
            report["rows"].append({
                "branch": branch,
                "remote_ref": f"{remote}/{branch}",
                "unmerged_commits": 0,
            })

    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_merged_remote_branches.py",
        description=(
            "Find remote-tracking branches on <remote> that are fully merged "
            "into <base-branch> (zero unmerged commits per `git cherry`). "
            "Emits a JSON report on stdout and exits 0 (advisory) / 1 (with "
            "--strict + hits) / 2 (repo missing, fetch failed, or git query "
            "error). Run via scripts/test_sweep_merged_remote_branches.sh "
            "for the contract."
        ),
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "Path to the git working tree. Defaults to $SWEEP_REPO or the "
            "current working directory. The bash test harness always passes "
            "--repo so the live repo is untouched."
        ),
    )
    parser.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help=(
            f"Remote name to scan. Defaults to {DEFAULT_REMOTE!r}. The "
            "sweeper enumerates refs/remotes/<remote>/* and runs `git "
            "cherry <base-branch> <remote>/<branch>` for each."
        ),
    )
    parser.add_argument(
        "--base-branch",
        default=DEFAULT_BASE_BRANCH,
        help=(
            f"Branch to compare against. Defaults to "
            f"{DEFAULT_BASE_BRANCH!r}. The base branch is always excluded "
            "from results — it's the comparison branch, not a target."
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="BRANCH",
        help=(
            "Skip this branch (in addition to <base-branch>). Repeatable: "
            "--exclude foo --exclude bar. Useful for protecting known-live "
            "branches that don't yet have a clean merge story."
        ),
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help=(
            "Skip `git fetch <remote>` and only enumerate whatever is "
            "already in refs/remotes/<remote>/*. Pure local mode for tests, "
            "CI, and offline use; without this flag the sweeper refreshes "
            "the cache first so dead branches merged between fetches are "
            "surfaced promptly."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any fully-merged branch is found. Default is "
            "advisory: exit 0 even with hits, mirroring the sibling "
            "sweepers (sweep_dangling_depends_on.py etc.)."
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

    repo = _resolve_repo(args.repo)
    excluded = {args.base_branch, *args.exclude}
    try:
        report = sweep(
            repo,
            remote=args.remote,
            base_branch=args.base_branch,
            excluded=excluded,
            do_fetch=not args.no_fetch,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Pass --repo=/path/to/repo or set SWEEP_REPO=/path/to/repo.",
            file=sys.stderr,
        )
        return 2
    except GitError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["fully_merged"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())