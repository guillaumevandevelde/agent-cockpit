"""Drift check 1: every `frontend/src/features/<name>/` folder should have a
corresponding `docs/features/<name>.md` (or an explicit alias).

Signal-only: writes a markdown summary to `/tmp/features_docs_summary.md` and
prints a short status line. The drift-report workflow appends the summary to
`$GITHUB_STEP_SUMMARY` and captures the status as a step output. The script
always exits 0; drift is information, not a build failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `scripts` importable when run as `python scripts/check_features_docs.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.drift_checks import find_missing_feature_docs  # noqa: E402

# Multi-feature docs that cover more than one folder under frontend/src/features/.
# Without an explicit alias the 1:1 check would surface these as drift.
DOC_ALIASES: dict[str, str] = {
    "agents": "agents-skills.md",
    "skills": "agents-skills.md",
    "mcp": "mcp-servers.md",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Path to the repository root (parent of frontend/ and docs/).",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("/tmp/features_docs_summary.md"),
        help="Where to write the markdown summary for the workflow to append.",
    )
    args = parser.parse_args()

    features_dir = args.repo_root / "frontend" / "src" / "features"
    docs_dir = args.repo_root / "docs" / "features"
    missing = find_missing_feature_docs(features_dir, docs_dir, aliases=DOC_ALIASES)

    if missing:
        lines = [
            "### Features → docs drift",
            "",
            f"**Status:** drifted — {len(missing)} feature folder(s) without a matching doc.",
            "",
            "Missing `docs/features/<name>.md` (alias-aware check):",
            "",
        ]
        lines.extend(f"- `{name}`" for name in missing)
        lines.append("")
        status = f"drifted: {len(missing)}"
    else:
        lines = [
            "### Features → docs drift",
            "",
            "**Status:** ok — every `frontend/src/features/*/` folder has a doc.",
            "",
        ]
        status = "ok"

    args.summary_out.write_text("\n".join(lines))
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
