"""Drift check 3: warn when `CLAUDE.md` has not been touched in many merge
commits. The "20 most recent merge commits" heuristic catches the case where
CLAUDE.md was last updated weeks/months ago while the project structure and
canonical agent roster keep moving underneath it.

Signal-only: writes a markdown summary and prints a short status line.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.drift_checks import claude_md_age_in_merges  # noqa: E402

# Drift threshold: how many merge commits on the default branch can land
# between the most recent CLAUDE.md update and HEAD before we flag it. Twenty
# is a soft signal — usually a sprint's worth of work.
DEFAULT_THRESHOLD = 20


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--file",
        default="CLAUDE.md",
        help="File to age-check, relative to the repo root.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help="Number of merges after the file's last touch before flagging stale.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("/tmp/claude_md_age_summary.md"),
        help="Where to write the markdown summary.",
    )
    args = parser.parse_args()

    count, stale = claude_md_age_in_merges(args.repo_root, args.file, args.threshold)

    lines = ["### CLAUDE.md age vs merge cadence", ""]
    if stale:
        status = f"stale: {count} merges since last CLAUDE.md update (threshold {args.threshold})"
        lines.append(
            f"**Status:** stale — {count} merge commit(s) have landed on the default "
            f"branch since the last update of `{args.file}` "
            f"(threshold: {args.threshold})."
        )
        lines.append("")
        lines.append(
            "The project orientation banner and cross-cutting project pointers "
            "in `CLAUDE.md` may be drifting from the current state — review and "
            "refresh as part of the next maintenance pass."
        )
    elif count == 0:
        status = "fresh: no merges after CLAUDE.md"
        lines.append(
            f"**Status:** fresh — `{args.file}` is the most recent commit on the "
            "default branch, or no merge commits have landed since it was last "
            "touched."
        )
    else:
        status = f"fresh: {count} merges since last update"
        lines.append(
            f"**Status:** fresh — {count} merge commit(s) on the default branch "
            f"since the last update of `{args.file}` "
            f"(threshold: {args.threshold})."
        )

    args.summary_out.write_text("\n".join(lines) + "\n")
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
