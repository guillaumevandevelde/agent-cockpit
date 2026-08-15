"""Diff the live FastAPI OpenAPI schema against a committed snapshot.

Only `paths` and `components` are compared. `info.version` tracks the app
version (bumped independently via scripts/bump-version.sh) and would make
every release look like an API contract change if included.

Modes (see `--help`):

- (default)   check the live spec against the snapshot; exit 1 on drift.
- --update    apply ONLY this branch's API changes onto the snapshot, leaving
              pre-existing drift from older commits untouched. Computes the
              branch's delta as `current_spec - merge_base_spec` and merges it
              into the committed snapshot (a structural three-way merge). This
              avoids the trap where a small deletion regenerates the whole
              snapshot and buries the real change under hundreds of unrelated
              drift lines.
- --full      wholesale regeneration: overwrite the snapshot with the current
              live spec (the old `--update` behaviour). Bakes in every drift.
- --dry-run   with --update/--full, print the proposed diff without writing.
- --emit      print the live contract shape as JSON to stdout (internal: used
              to emit the merge-base spec from a throwaway worktree).
"""
import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).parent.parent
REPO_ROOT = BACKEND_ROOT.parent
SNAPSHOT_PATH = BACKEND_ROOT / "openapi.snapshot.json"

_MISSING = object()


class BaseSpecUnavailable(RuntimeError):
    """Raised when the merge-base spec needed for a targeted update can't be built."""


# Paths whose presence depends on the environment rather than on the API.
# `app/main.py` mounts the built SPA at "/" when `frontend/dist` exists, and
# registers a plain `@app.get("/")` when it does not. So "/" shows up in the
# live spec purely based on whether a frontend build happens to be on disk: a
# dev checkout with a build writes a snapshot that a CI runner without one
# rejects, and the other way round. That is what turned quality.yml red on
# master (2026-08-13 .. 2026-08-15) while the same check passed locally.
# Every real API path lives under /api/v1, so dropping "/" from the compared
# shape makes the gate deterministic in both environments.
_ENV_DEPENDENT_PATHS = ("/",)


def contract_shape(schema: dict) -> dict:
    paths = {
        path: item
        for path, item in schema["paths"].items()
        if path not in _ENV_DEPENDENT_PATHS
    }
    return {"paths": paths, "components": schema.get("components", {})}


def current_shape(app_root: Path = BACKEND_ROOT) -> dict:
    # Running as `python scripts/check_openapi_snapshot.py` puts scripts/ (not
    # backend/) at sys.path[0], so `app` isn't importable without this. When
    # emitting the merge-base spec we point this at a throwaway worktree's
    # backend/ so we import that revision's app instead.
    sys.path.insert(0, str(app_root))
    from app.main import app

    return contract_shape(app.openapi())


def snapshot_text(shape: dict) -> str:
    return json.dumps(shape, indent=2, sort_keys=True) + "\n"


def three_way_merge(snapshot: dict, base: dict, current: dict) -> dict:
    """Apply only the `base -> current` changes onto `snapshot`.

    This isolates *this branch's* API changes (`current - base`, where `base`
    is the merge-base spec) and merges them into the committed snapshot, so a
    targeted update never drags in drift that predates the branch. Keys where
    `base == current` are left exactly as the snapshot has them.
    """
    result = dict(snapshot)
    for key in set(base) | set(current):
        b = base.get(key, _MISSING)
        c = current.get(key, _MISSING)
        if b == c:
            continue  # the branch didn't touch this key relative to base
        if c is _MISSING:
            result.pop(key, None)  # the branch removed it
        elif (
            isinstance(b, dict)
            and isinstance(c, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = three_way_merge(result[key], b, c)
        else:
            result[key] = c
    return result


def unified_diff(old_text: str, new_text: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="openapi.snapshot.json",
            tofile="openapi.snapshot.json (proposed)",
        )
    )


def _changed_line_count(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
    )


def _merge_base_ref() -> str | None:
    for ref in ("origin/master", "master"):
        r = _git("merge-base", "HEAD", ref)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def emit_base_spec() -> dict:
    """Emit the OpenAPI contract shape of the merge-base against master.

    Checks the merge-base out into a throwaway `--detach`ed worktree and runs
    this same script in `--emit` mode there (fresh interpreter, current venv),
    so we get *that revision's* API surface without mutating the working tree.
    """
    ref = _merge_base_ref()
    if ref is None:
        raise BaseSpecUnavailable("could not determine a merge-base against master")

    tmp = tempfile.mkdtemp(prefix="openapi-base-")
    worktree = Path(tmp) / "wt"
    try:
        add = _git("worktree", "add", "--detach", str(worktree), ref)
        if add.returncode != 0:
            raise BaseSpecUnavailable(f"git worktree add failed: {add.stderr.strip()}")
        env = {**os.environ, "OPENAPI_APP_ROOT": str(worktree / "backend")}
        emit = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--emit"],
            capture_output=True,
            text=True,
            env=env,
        )
        if emit.returncode != 0:
            raise BaseSpecUnavailable(
                f"emitting merge-base spec failed: {emit.stderr.strip()}"
            )
        return json.loads(emit.stdout)
    finally:
        _git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(tmp, ignore_errors=True)


def _write_snapshot(shape: dict) -> None:
    SNAPSHOT_PATH.write_text(snapshot_text(shape))


def run_update(*, full: bool, dry_run: bool) -> int:
    current = current_shape()
    old_text = SNAPSHOT_PATH.read_text() if SNAPSHOT_PATH.exists() else ""

    if full or not SNAPSHOT_PATH.exists():
        new_shape = current
        mode = "full"
    else:
        snapshot = json.loads(old_text)
        try:
            base = emit_base_spec()
        except BaseSpecUnavailable as exc:
            print(
                f"Could not compute a targeted update ({exc}).\n"
                "Re-run with --full to regenerate the whole snapshot instead.",
                file=sys.stderr,
            )
            return 1
        new_shape = three_way_merge(snapshot, base, current)
        mode = "targeted"

    new_text = snapshot_text(new_shape)
    diff = unified_diff(old_text, new_text)
    if not diff:
        print("Snapshot already up to date; nothing to write.")
        return 0

    changed = _changed_line_count(diff)
    if dry_run:
        print(diff, end="")
        print(f"\n[dry-run] {mode} update would change ~{changed} line(s); not writing.")
        return 0

    if changed > 50:
        print(
            f"Warning: {mode} update changes ~{changed} lines. Re-run with "
            "--dry-run first if that's more than you expected.",
            file=sys.stderr,
        )
    _write_snapshot(new_shape)
    print(f"Updated {SNAPSHOT_PATH} ({mode}, ~{changed} line(s)).")
    return 0


def run_check() -> int:
    current = current_shape()

    if not SNAPSHOT_PATH.exists():
        _write_snapshot(current)
        print(f"Created {SNAPSHOT_PATH}")
        return 0

    # Normalise the committed snapshot through the same filter, so a snapshot
    # written before _ENV_DEPENDENT_PATHS existed still compares cleanly.
    snapshot = contract_shape(json.loads(SNAPSHOT_PATH.read_text()))
    if current != snapshot:
        print(
            "API surface changed but backend/openapi.snapshot.json was not "
            "updated.\n\n"
            "Run: cd backend && python scripts/check_openapi_snapshot.py --update\n"
            "(--update applies only this branch's changes; use --full to "
            "regenerate the whole snapshot.)\n"
            "Then check whether the frontend's hand-maintained TypeScript "
            "types (frontend/src/types/) need matching updates, and commit "
            "both.",
            file=sys.stderr,
        )
        return 1

    print("OpenAPI contract matches snapshot.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--update",
        action="store_true",
        help="apply only this branch's API changes onto the snapshot",
    )
    group.add_argument(
        "--full",
        action="store_true",
        help="wholesale-regenerate the snapshot from the live spec (bakes in drift)",
    )
    group.add_argument(
        "--emit",
        action="store_true",
        help="print the live contract shape as JSON (internal)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --update/--full, print the proposed diff without writing",
    )
    args = parser.parse_args(argv)

    if args.emit:
        app_root = Path(os.environ.get("OPENAPI_APP_ROOT", str(BACKEND_ROOT)))
        sys.stdout.write(json.dumps(current_shape(app_root)))
        return 0

    if args.update or args.full:
        return run_update(full=args.full, dry_run=args.dry_run)

    return run_check()


if __name__ == "__main__":
    sys.exit(main())
