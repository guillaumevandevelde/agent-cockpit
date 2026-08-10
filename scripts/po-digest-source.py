#!/usr/bin/env python3
"""Collect the raw weekly building blocks for the PO-digest as JSON on stdout.

Mechanical half of the wekelijkse product-owner-digest (see
``docs/cockpit/po-digest-design.md`` §6). The skill redacts and clusters this
output; THIS script interprets nothing. Two reasons for the split:

1. *Determinism.* Without a single source of truth for the window and
   dedupe rules, every weekly session writes its own SQL and the definition
   of "this week" drifts silently.
2. *Sub-second latency.* The collector runs ~50ms against a fixture DB;
   the skill spends its context budget on redactie, not on strftime.

Contract:

   python3 scripts/po-digest-source.py [--since ISO] [--until ISO]
                                        [--project-key KEY]
                                        [--kanban-db PATH]
                                        [--repo-root PATH]
                                        [--backend-base-url URL]

Output: a single JSON object on stdout:

    {
      "window": {"since": "<ISO>", "until": "<ISO>"},
      "shipped": [
          {"card_id": "<id>", "title": "<title>", "summary": "<text>",
           "at": "<ISO>", "ops_seq": <int>}
      ],
      "decisions": [
          {"row": "<verbatim row text>", "committed_at": "<ISO>"}
      ],
      "waiting": [
          {"card_id": "...", "card_title": "...", "kind": "...",
           "reason": "...", "created_at": "<ISO>", "wait_seconds": <int>}
      ],
      "course_changes": [
          {"kind": "reversal" | "outcome_not_feasible" | "outcome_no_action_needed" | "reopen",
           "row": "<...>", "card_id": "<id>", "summary": "<text>",
           "at": "<ISO>"}
      ],
      "errors": {"<key>": "<message>"}   # only present when something went wrong
    }

When ``--since`` is omitted, the window is derived from the newest file under
``docs/cockpit/po-digest/`` (the collector refuses to depend on
``.claude/state/`` because that path is gitignored and the worktree is fresh
on every dispatch — see spec §6.1). If no prior week file exists, the
collector falls back to ``now − 7d``. ``--until`` defaults to ``now``.

Exit codes: 0 on success (regardless of empty sections), 2 on usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Mirrors backend/app/kanban/service.py::_DONE_SUMMARY_PREFIX. The literal
# ``**Summary:** `` is the canonical Done-summary label that mcp_server.move_card
# posts when a card lands in Done; matching on it keeps the collector in
# lock-step with the enrichment read by the live Done-column API.
SUMMARY_PREFIX = "**Summary:** "

# Mirrors the comment prefixes in backend/app/kanban/service.py
# (`_REVIEW_REQUESTED_PREFIX`, `_ROUTING_MISMATCH_PREFIX`, …). The outcome
# labels are written by the analyst when it moves an analysis card to Done —
# see docs/cockpit/analysis-outcome-contract-decision.md.
OUTCOME_NOT_FEASIBLE = "**Outcome:** not_feasible"
OUTCOME_NO_ACTION = "**Outcome:** no_action_needed"

# Same meaning as the regex in scripts/check-decision-register.sh: a row that
# is part of a decision reversal, planted into decisions.md by the analyst
# when a decision is reopened (see spec §3 sectie 4).
REVERSAL_MARKER = "↩︎ herzien door"

# Fallback path for the kanban DB. Mirrors backend/app/config.py::
# _default_kanban_database_url so the script reads the same store the
# live app reads from. Override via --kanban-db or $KANBAN_DB; the CLI flag
# wins when both are set.
DEFAULT_KANBAN_DB = "~/.claude-registry/kanban.db"

# Path the collector walks for the prior-week file when --since is omitted.
# Mirrors the spec §6.1 contract: doc-based, not state-based.
DEFAULT_DIGEST_DIRNAME = "docs/cockpit/po-digest"

# Week-filename token (e.g. ``2026-W33``) extracted from the path stem.
# Mirrors the writer side: weekly digests land as ``<year>-W<ww>.md`` and
# sort lexicographically = chronologically under ISO-8601.
_WEEK_FILE_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")

# Default backend base URL. The wachtrij lives behind this — see
# backend/app/api/v1/kanban/router.py:467 (po_wachtrij endpoint).
DEFAULT_BACKEND_BASE_URL = "http://localhost:8000"

# Implementation note: the success-contract is "empty sections render as
# lists, not null". ``errors`` is the only key that may be absent; when
# present, it carries per-section failure messages so the skill can decide
# whether to redact them into the digest or skip the section.
SHIPPED_KEY = "shipped"
DECISIONS_KEY = "decisions"
WAITING_KEY = "waiting"
COURSE_CHANGES_KEY = "course_changes"
WINDOW_KEY = "window"
ERRORS_KEY = "errors"


# ---------------------------------------------------------------------------
# CLI / window resolution
# ---------------------------------------------------------------------------

def _parse_iso_datetime(s: str) -> datetime:
    """Parse an ISO-8601 string; accept ``Z`` alias for ``+00:00``.

    SQLite stores naive datetimes; the collector normalizes both to
    tz-aware UTC before any subtraction so the boundary checks don't trip
    on a naive/aware mismatch.
    """
    if not s:
        raise ValueError("empty datetime")
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _resolve_db_path(cli_arg: str | None) -> Path:
    """Same precedence as scripts/sweep_dangling_depends_on.py: CLI > env > default.."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_DB")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_KANBAN_DB).expanduser().resolve()


def _resolve_repo_root(cli_arg: str | None) -> Path:
    """Walked for the prior-week file. CLI > env > fall back to *this* script's
    parent, which is the repo root when the script lives in scripts/.
    """
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("PO_DIGEST_DIR")
    if env:
        # PO_DIGEST_DIR is a direct override for the digests subdir — let
        # the caller bypass the docs/cockpit/po-digest derivation.
        return Path(env).expanduser().resolve()
    # Default: this script is <repo>/scripts/po-digest-source.py → repo root is
    # the parent of THIS script's directory.
    return Path(__file__).resolve().parent.parent


def _resolve_digest_dir(repo_root: Path) -> Path:
    """Return the directory the collector walks for prior-week files. Honors
    PO_DIGEST_DIR (which actually points AT the digests subdir directly) and
    otherwise falls back to ``<repo>/docs/cockpit/po-digest/``.
    """
    env = os.environ.get("PO_DIGEST_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return repo_root / DEFAULT_DIGEST_DIRNAME


def _resolve_backend_base_url(cli_arg: str | None) -> str:
    if cli_arg:
        return cli_arg.rstrip("/")
    env = os.environ.get("BACKEND_BASE_URL")
    if env:
        return env.rstrip("/")
    return DEFAULT_BACKEND_BASE_URL


def _parse_frontmatter_date(text: str, key: str) -> datetime | None:
    """Pull ``key: <ISO>`` out of a tiny YAML frontmatter block.

    The week files are written by the skill as plain markdown with a
    minimal frontmatter (until: …). Anything fancier (TOML, multiline
    values, escapes) is out of scope — the collector is the *only*
    consumer of this block, so the format is fixed by the writer side.
    """
    # Character class: digits, iso separators (T, :, -, .), and the trailing
    # Z. Single-quoted / double-quoted values are accepted (the writer side
    # may or may not quote the date).
    m = re.search(rf"^\s*{re.escape(key)}:\s*['\"]?([0-9T:Z:+\-.]+)['\"]?\s*$",
                  text, re.MULTILINE)
    if not m:
        return None
    try:
        return _parse_iso_datetime(m.group(1))
    except ValueError:
        return None


def _resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime, dict[str, str]]:
    """Return the (since, until, errors) the collector will use.

    See spec §6.1 for the rationale. Three cases for ``--since``:

    1. Explicit → use it as-is.
    2. Omitted + a prior week file with a parseable ``until:`` frontmatter
       exists → since = that file's ``until:`` (self-correcting after a
       missed week). Selection sorts by the ``YYYY-Www`` token in the
       filename, NOT by mtime — a fresh git checkout stamps every file with
       the same mtime, so mtime-based ordering degrades to iterdir()
       order and may land on a non-week file (e.g. ``README.md``).
    3. Omitted + no usable prior week file → since = now − 7d. The
       fallback is reported on ``errors["window_fallback"]`` so a silent
       double-count cannot recur (kaart df54a63d…).

    ``--until`` defaults to ``now`` when omitted.
    """
    now = datetime.now(UTC)
    explicit_until = _parse_iso_datetime(args.until) if args.until else now
    errors: dict[str, str] = {}
    if args.since:
        return _parse_iso_datetime(args.since), explicit_until, errors
    repo_root = _resolve_repo_root(args.repo_root)
    digest_dir = _resolve_digest_dir(repo_root)
    if digest_dir.is_dir():
        # Sort by the YYYY-Www token in the filename — lexicographic on a
        # fixed-width ISO week key equals chronological. mtime is
        # meaningless in a fresh checkout (every file gets the same stamp),
        # and an unrelated file like README.md can land in iterdir() order
        # before the real week files.
        files = [
            p for p in digest_dir.iterdir()
            if p.is_file() and p.suffix == ".md"
        ]
        files.sort(key=lambda p: (
            # Week files sort first and newest-first. Non-week files follow,
            # so an incidental `until:` line in README.md cannot win.
            _WEEK_FILE_RE.match(p.stem) is not None,
            p.stem,
        ), reverse=True)
        for candidate in files:
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            until = _parse_frontmatter_date(text, "until")
            if until is not None:
                return until, explicit_until, errors
            # File is a week-named candidate without a usable until → keep
            # looking. README.md / out-of-band notes never had one.
        # Walked the whole digest dir without finding a usable until.
        if files:
            reason = "no prior week file has a parseable `until:` frontmatter"
        else:
            reason = "digest directory contains no Markdown files"
    else:
        reason = "digest directory does not exist"
    errors["window_fallback"] = (
        f"{reason} at {digest_dir}; falling back to now − 7d"
    )
    return now - timedelta(days=7), explicit_until, errors


# ---------------------------------------------------------------------------
# Kanban DB queries (read-only)
# ---------------------------------------------------------------------------

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """Read-only connection; missing/unreadable DBs are reported via the
    ``errors`` block rather than a crash so the weeks after a board reset
    don't break the dispatch.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"kanban DB not found at {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


_SUMMARY_OPS_SQL = """
    SELECT
        o.entity_id,
        json_extract(o.payload, '$.text'),
        o.created_at,
        o.payload
    FROM kanban_ops o
    WHERE o.op_type = 'comment'
      AND json_extract(o.payload, '$.text') LIKE :prefix
      AND o.created_at >= :since
      AND o.created_at < :until
    ORDER BY o.created_at DESC
"""

_CREATE_OPS_SQL = """
    SELECT entity_id, json_extract(payload, '$.title')
    FROM kanban_ops
    WHERE op_type = 'create'
      AND entity_id IN ({ids})
"""

_OUTCOME_OPS_SQL = """
    SELECT entity_id, json_extract(payload, '$.text'), created_at
    FROM kanban_ops
    WHERE op_type = 'comment'
      AND (
        json_extract(payload, '$.text') LIKE :out_a
        OR json_extract(payload, '$.text') LIKE :out_b
      )
      AND created_at >= :since
      AND created_at < :until
    ORDER BY created_at DESC
"""

_REOPEN_OPS_SQL = """
    SELECT entity_id, created_at
    FROM kanban_ops
    WHERE op_type = 'reopen'
      AND created_at >= :since
      AND created_at < :until
    ORDER BY created_at DESC
"""


def _format_iso(dt: str) -> str:
    """Normalize op-created_at strings to ISO-8601. SQLite stores them as
    naive ISO without tzinfo; we strip trailing Z and trust the reader
    to treat them as UTC (the kanban DB is UTC-only — see
    backend/app/kanban/db.py header).
    """
    return dt.replace(" ", "T")


def _sqlite_datetime_bound(dt: datetime) -> str:
    """Match SQLAlchemy's UTC-naive SQLite ``DateTime`` storage format."""
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _query_shipped(conn: sqlite3.Connection, since: datetime, until: datetime) -> list[dict]:
    """Per card-id the newest ``**Summary:**`` comment in the window + the
    title from the create-op (or kanban_cards if the create-op is gone).
    """
    cur = conn.execute(
        _SUMMARY_OPS_SQL,
        {
            "prefix": SUMMARY_PREFIX + "%",
            "since": _sqlite_datetime_bound(since),
            "until": _sqlite_datetime_bound(until),
        },
    )
    # Newest per entity_id
    newest: dict[str, tuple[str, str]] = {}
    for entity_id, text, created_at, _payload in cur.fetchall():
        if entity_id in newest:
            continue
        newest[entity_id] = (text[len(SUMMARY_PREFIX):], created_at)

    if not newest:
        return []

    # Look up titles from create-op first (survives card deletion); fall back
    # to kanban_cards.title if the create-op is missing for some reason.
    ids = list(newest.keys())
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(_CREATE_OPS_SQL.format(ids=placeholders), ids)
    titles = {entity_id: title for entity_id, title in cur.fetchall()}

    if len(titles) < len(ids):
        cur = conn.execute(
            f"SELECT id, title FROM kanban_cards WHERE id IN ({placeholders})",
            ids,
        )
        for cid, title in cur.fetchall():
            titles.setdefault(cid, title)

    shipped = []
    for cid, (summary, created_at) in newest.items():
        shipped.append({
            "card_id": cid,
            "title": titles.get(cid, ""),
            "summary": summary,
            "at": _format_iso(created_at),
        })
    # Stable order: newest first
    shipped.sort(key=lambda x: x["at"], reverse=True)
    return shipped


def _query_course_changes(conn: sqlite3.Connection, since: datetime, until: datetime) -> list[dict]:
    """Outcome comments + reopen ops in the window. Reversions live in
    decisions.md (git-extracted), not the op-log, so they are joined in
    via the caller below.
    """
    out: list[dict] = []
    cur = conn.execute(
        _OUTCOME_OPS_SQL,
        {
            "out_a": OUTCOME_NOT_FEASIBLE + "%",
            "out_b": OUTCOME_NO_ACTION + "%",
            "since": _sqlite_datetime_bound(since),
            "until": _sqlite_datetime_bound(until),
        },
    )
    for entity_id, text, created_at in cur.fetchall():
        if text.startswith(OUTCOME_NOT_FEASIBLE):
            kind = "outcome_not_feasible"
        elif text.startswith(OUTCOME_NO_ACTION):
            kind = "outcome_no_action_needed"
        else:
            continue
        out.append({
            "kind": kind,
            "card_id": entity_id,
            "summary": text.split(" ", 1)[-1] if " " in text else "",
            "at": _format_iso(created_at),
        })

    cur = conn.execute(
        _REOPEN_OPS_SQL,
        {
            "since": _sqlite_datetime_bound(since),
            "until": _sqlite_datetime_bound(until),
        },
    )
    for entity_id, created_at in cur.fetchall():
        out.append({
            "kind": "reopen",
            "card_id": entity_id,
            "at": _format_iso(created_at),
        })

    out.sort(key=lambda x: x["at"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# decisions.md via git
# ---------------------------------------------------------------------------

def _decisions_via_git(repo_root: Path, since: datetime, until: datetime) -> list[dict]:
    """Run ``git log`` against ``docs/cockpit/decisions.md`` and parse the
    added table rows. Dedupe by normalized row text — the spec is explicit
    that ``--no-merges`` does NOT remove the duplicates we care about
    (rebase / amend / cherry-pick re-add the same line in multiple commits).
    """
    if not (repo_root / ".git").exists():
        return []
    # Format: %H = commit, %aI = author date (ISO), so we can filter by
    # date in python and avoid the locale-dependent output of --since.
    cmd = ["git", "-C", str(repo_root), "log", "--no-merges",
           "--format=%H%x09%aI", "-p", "--", "docs/cockpit/decisions.md"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        return []
    rows: dict[str, dict] = {}
    commit_date = None
    for line in out.stdout.splitlines():
        if line.startswith("commit "):
            # Most git versions print `commit <sha>` on its own line; the
            # custom --format lines are interleaved with the diff. We use a
            # tab separator so we can detect our own lines.
            continue
        if "\t" in line and re.match(r"^[0-9a-f]{40}\t", line):
            sha, iso = line.split("\t", 1)
            try:
                commit_date = _parse_iso_datetime(iso)
            except ValueError:
                commit_date = None
            continue
        if not line.startswith("+|"):
            continue
        if commit_date is None:
            continue
        if not (since <= commit_date < until):
            continue
        row_text = line[1:].strip()
        # Reversal rows belong in `course_changes` (spec §3 row 4), not in
        # `decisions` — they're how we signal "an earlier decision was
        # reopened", not a fresh direction. Exclude them here so the two
        # lists stay disjoint.
        if REVERSAL_MARKER in row_text:
            continue
        # Normalize whitespace runs to fold identical rows that differ only
        # in collapsing (rare, but the spec says dedupe on genormaliseerde
        # rijtekst — be tolerant on whitespace so the digest doesn't show
        # the same decision twice).
        norm = re.sub(r"\s+", " ", row_text)
        if norm in rows:
            continue
        rows[norm] = {"row": row_text, "committed_at": commit_date.isoformat()}
    # Newest-first by commit date
    return sorted(rows.values(), key=lambda x: x["committed_at"], reverse=True)


def _reversals_via_git(repo_root: Path, since: datetime, until: datetime) -> list[dict]:
    """Pull ``↩︎ herzien door`` rows from the same git window. These are the
    sectie-4 trigger per spec §3 row 4.
    """
    if not (repo_root / ".git").exists():
        return []
    cmd = ["git", "-C", str(repo_root), "log", "--no-merges",
           "--format=%H%x09%aI", "-p", "--", "docs/cockpit/decisions.md"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        return []
    rows: dict[str, dict] = {}
    commit_date = None
    for line in out.stdout.splitlines():
        if "\t" in line and re.match(r"^[0-9a-f]{40}\t", line):
            _, iso = line.split("\t", 1)
            try:
                commit_date = _parse_iso_datetime(iso)
            except ValueError:
                commit_date = None
            continue
        if not line.startswith("+"):
            continue
        if REVERSAL_MARKER not in line:
            continue
        if commit_date is None:
            continue
        if not (since <= commit_date < until):
            continue
        row_text = line[1:].strip()
        norm = re.sub(r"\s+", " ", row_text)
        if norm in rows:
            continue
        rows[norm] = {"kind": "reversal", "row": row_text, "at": commit_date.isoformat()}
    return sorted(rows.values(), key=lambda x: x["at"], reverse=True)


# ---------------------------------------------------------------------------
# Waiting (call into the live backend)
# ---------------------------------------------------------------------------

def _query_waiting(project_key: str, backend_base_url: str) -> tuple[list[dict], str | None]:
    """GET /api/v1/kanban/wachtrij?project_key=… → list of items.

    Returns ``(items, error_msg)``. On backend-down / non-2xx, the items
    list is empty and error_msg describes the failure so the skill can
    decide whether to surface that in the digest.
    """
    url = f"{backend_base_url}/api/v1/kanban/wachtrij?project_key={urllib.parse.quote(project_key)}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return [], f"backend unreachable at {backend_base_url}: {e}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        return [], f"wachtrij response was not JSON: {e}"
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return [], "wachtrij response shape unexpected: missing 'items' list"
    return items, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="po-digest-source.py",
        description=(
            "Collect the raw weekly building blocks for the PO-digest as JSON "
            "on stdout. See docs/cockpit/po-digest-design.md §6."
        ),
    )
    p.add_argument("--since", help="ISO-8601; default = newest prior week file's `until:` (or now − 7d)")
    p.add_argument("--until", help="ISO-8601; default = now")
    p.add_argument("--project-key", default="",
                   help="Kanban project_key (used for the wachtrij call).")
    p.add_argument("--kanban-db", help="Path to kanban DB (default: ~/.claude-registry/kanban.db)")
    p.add_argument("--repo-root", help="Path to the repo root (default: <script_dir>/..)")
    p.add_argument("--backend-base-url", help="Cockpit backend URL (default: http://localhost:8000)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    since, until, window_errors = _resolve_window(args)
    errors: dict[str, str] = dict(window_errors)
    out: dict = {
        WINDOW_KEY: {"since": since.isoformat(), "until": until.isoformat()},
        SHIPPED_KEY: [],
        DECISIONS_KEY: [],
        WAITING_KEY: [],
        COURSE_CHANGES_KEY: [],
    }

    # --- shipped + course_changes (kanban DB) --------------------------------
    db_path = _resolve_db_path(args.kanban_db)
    try:
        with _connect_ro(db_path) as conn:
            out[SHIPPED_KEY] = _query_shipped(conn, since, until)
            out[COURSE_CHANGES_KEY] = _query_course_changes(conn, since, until)
    except (FileNotFoundError, sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        errors["shipped"] = f"kanban DB unreadable: {e}"
        # course_changes is half-board, half-git; the op-log half fails the
        # same way as shipped, so report it under the same key.

    # --- decisions + reversal rows (git) ------------------------------------
    repo_root = _resolve_repo_root(args.repo_root)
    digest_dir = _resolve_digest_dir(repo_root)
    out[DECISIONS_KEY] = _decisions_via_git(repo_root, since, until)
    reversals = _reversals_via_git(repo_root, since, until)
    # Reversals are part of course_changes; most recent first.
    out[COURSE_CHANGES_KEY] = sorted(
        list(out[COURSE_CHANGES_KEY]) + reversals,
        key=lambda x: x.get("at", ""),
        reverse=True,
    )

    # --- waiting (backend call) ---------------------------------------------
    backend_base_url = _resolve_backend_base_url(args.backend_base_url)
    if args.project_key:
        items, err = _query_waiting(args.project_key, backend_base_url)
        if err:
            errors[WAITING_KEY] = err
        else:
            out[WAITING_KEY] = items
    else:
        errors[WAITING_KEY] = "no --project-key supplied; wachtrij skipped"

    if errors:
        out[ERRORS_KEY] = errors

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
