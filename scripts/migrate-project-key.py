#!/usr/bin/env python3
"""Re-key a project across both SQLite stores after its git remote changed.

The board's identity is derived, not stored: ``resolve_project_key()``
(``backend/app/kanban/project_key.py``) shells out to ``git remote get-url
origin`` on every request. Rename the repo on the forge *and* update the local
remote, and every card/column/meta/gate row silently belongs to a project key
that nothing resolves to any more — the board reads as empty and autodispatch
config is lost. Nothing errors; the rows are simply orphaned.

This script rewrites the old key to the new one across both stores (see the
CLAUDE.md "Twee aparte SQLite-stores" gotcha — the kanban board and the
registry each hold project-keyed rows) and optionally flips the git remote in
the same run, so the two never drift apart.

Dry-run by default. Refuses to touch a board with a live agent claim unless
``--force``: re-keying under a running dispatcher moves the card out from under
a session that still holds it.

Scope: **remote**-derived identity only. Path-derived columns
(``resume_project_folder``, ``session_cache.project_folder``,
``usage_cache.project_path``, ``mail_*``, ``project_security_profiles``) encode
the on-disk checkout location and are untouched — they only change if the
working directory itself is renamed, which is a separate migration.

Usage::

    scripts/migrate-project-key.py --to-key git:github.com/OWNER/NEW       # dry-run
    scripts/migrate-project-key.py --to-key git:github.com/OWNER/NEW --apply
    scripts/migrate-project-key.py --new-remote git@github.com:OWNER/NEW.git \\
        --apply --update-remote
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KANBAN_DB = Path.home() / ".claude-registry" / "kanban.db"
REGISTRY_DB = REPO_ROOT / "backend" / "claude_registry.db"
BACKUP_DIR = Path.home() / ".claude-registry" / "backups"

# (db, table, column, match) — "exact" rewrites the whole value, "prefix"
# rewrites the key wherever it is embedded (kanban_meta namespaces the key
# behind a "<setting>:" prefix and sometimes suffixes a card id).
TARGETS = [
    ("kanban", "kanban_ops", "project_key", "exact"),
    ("kanban", "kanban_cards", "project_key", "exact"),
    ("kanban", "kanban_columns", "project_key", "exact"),
    ("kanban", "kanban_gates", "project_key", "exact"),
    ("kanban", "kanban_meta", "key", "prefix"),
    ("registry", "security_audit", "project_key", "exact"),
]


def resolve_key(path: Path) -> str:
    """The key the backend would compute for this checkout, right now."""
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.kanban.project_key import resolve_project_key

    return resolve_project_key(str(path))


def live_claims(conn: sqlite3.Connection, key: str) -> list[tuple[str, str, str]]:
    return conn.execute(
        "select id, column, claimed_by from kanban_cards "
        "where project_key = ? and column != 'Done' and claimed_by is not null",
        (key,),
    ).fetchall()


def count(conn: sqlite3.Connection, table: str, col: str, key: str, match: str) -> int:
    if match == "exact":
        sql, arg = f"select count(*) from {table} where {col} = ?", key
    else:
        sql, arg = f"select count(*) from {table} where {col} like ?", f"%{key}%"
    try:
        return conn.execute(sql, (arg,)).fetchone()[0]
    except sqlite3.OperationalError as e:
        print(f"  ! skipping {table}.{col}: {e}")
        return 0


def rewrite(conn, table: str, col: str, old: str, new: str, match: str) -> int:
    if match == "exact":
        cur = conn.execute(f"update {table} set {col} = ? where {col} = ?", (new, old))
    else:
        cur = conn.execute(
            f"update {table} set {col} = replace({col}, ?, ?) where {col} like ?",
            (old, new, f"%{old}%"),
        )
    return cur.rowcount


def backup(db: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{db.stem}.pre-rekey-{time.strftime('%Y%m%d-%H%M%S')}.db"
    # sqlite3's own backup API copies a consistent snapshot even mid-write,
    # which a plain file copy does not guarantee while the backend holds a
    # connection.
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-key", help="old project key (default: resolve from this checkout)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-key", help="new project key, e.g. git:github.com/OWNER/REPO")
    g.add_argument("--new-remote", help="new remote URL; the key is derived from it")
    p.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    p.add_argument("--update-remote", metavar="URL", nargs="?", const=True,
                   help="also set origin to this URL (defaults to --new-remote)")
    p.add_argument("--force", action="store_true", help="proceed despite live agent claims")
    args = p.parse_args()

    if args.new_remote:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app.kanban.project_key import normalize_remote

        new_key = f"git:{normalize_remote(args.new_remote)}"
    else:
        new_key = args.to_key

    old_key = args.from_key or resolve_key(REPO_ROOT)
    if old_key == new_key:
        print(f"Nothing to do: key is already {new_key}")
        return 0

    print(f"  from: {old_key}\n    to: {new_key}")
    print(f"  mode: {'APPLY' if args.apply else 'dry-run'}\n")

    conns = {}
    for label, db in (("kanban", KANBAN_DB), ("registry", REGISTRY_DB)):
        if not db.exists():
            print(f"! {label} db not found at {db} — skipping")
            continue
        conns[label] = sqlite3.connect(db)

    if "kanban" in conns:
        claims = live_claims(conns["kanban"], old_key)
        if claims:
            print(f"! {len(claims)} card(s) still held by an agent:")
            for cid, col, by in claims[:10]:
                print(f"    {cid[:8]}… [{col}] claimed_by={by}")
            if not args.force:
                print("\nRefusing to re-key under a live dispatcher. Let these "
                      "sessions finish, or pass --force.")
                return 1
            print("  --force given; continuing anyway.\n")

    plan = []
    for label, table, col, match in TARGETS:
        if label not in conns:
            continue
        n = count(conns[label], table, col, old_key, match)
        if n:
            plan.append((label, table, col, match, n))
            print(f"  {label}.{table}.{col:12s} {n:6d} rows  ({match})")
    total = sum(r[4] for r in plan)
    print(f"\n  total: {total} rows")

    if not args.apply:
        print("\nDry-run — nothing written. Re-run with --apply.")
        return 0
    if not total:
        print("\nNo rows to rewrite.")
        return 0

    for label in {r[0] for r in plan}:
        db = KANBAN_DB if label == "kanban" else REGISTRY_DB
        print(f"\n  backup {label} -> {backup(db)}")

    written = 0
    for label, table, col, match, _ in plan:
        conn = conns[label]
        with conn:  # one transaction per statement group; rolls back on error
            written += rewrite(conn, table, col, old_key, new_key, match)
    print(f"\n  rewrote {written} rows")

    for label, table, col, match in TARGETS:
        if label in conns:
            left = count(conns[label], table, col, old_key, match)
            if left:
                print(f"  ! {label}.{table}.{col} still has {left} old-key rows")

    if args.update_remote:
        url = args.update_remote if isinstance(args.update_remote, str) else args.new_remote
        if not url:
            print("\n! --update-remote needs a URL (or use it with --new-remote)")
            return 1
        subprocess.run(["git", "-C", str(REPO_ROOT), "remote", "set-url", "origin", url],
                       check=True)
        print(f"\n  origin -> {url}")
        now = resolve_key(REPO_ROOT)
        print(f"  resolve_project_key() now returns: {now}")
        if now != new_key:
            print(f"  ! MISMATCH — expected {new_key}")
            return 1
    else:
        print("\n  origin left unchanged. The board is now keyed to the new "
              "value but\n  resolve_project_key() still returns the old one — "
              "run `git remote set-url\n  origin <new-url>` to close the gap.")

    print("\nDone. Restart the backend so it drops any cached key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
