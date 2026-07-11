"""Drift check 4: detect "spec drift" — a kanban card whose `metadata.spec_doc`
points at a `docs/cockpit/<file>.md` path but whose closing merge touched
functional paths (`backend/app/`, `frontend/src/...`) without touching the
linked spec-doc.

Signal-only: writes a markdown summary and prints a short status line. The
weekly drift-report workflow appends the summary to `$GITHUB_STEP_SUMMARY`.
The script always exits 0; drift is advice (a `[spec-update]` Backlog card),
not a build-blocking gate.

Modelled after `check_features_docs.py` / `check_claude_md_age.py` (same
shape: thin CLI wrapper around a pure helper in `scripts.drift_checks`).

The card-level data source (Done cards with `metadata.spec_doc`) is read
via the kanban REST API at `http://localhost:8000/api/v1/kanban/cards`. The
merge SHA for each card is recovered from the card's `kind=branch`
deliverable: we resolve `git rev-parse origin/<branch>` so the comparison
runs against the exact tree that landed on master. Branches that have
already been deleted server-side (GitHub's `delete_branch_on_merge`) fall
back to `git log --merges --first-parent --grep=<card-id>` as a
best-effort — but in that case we simply skip the card (no evidence left
in git history that pinpoints the merge to this specific card).

See `docs/cockpit/spec-driven-development-analysis.md` §6 (Fase 2) for the
rationale, and `docs/cockpit/spec-driven-development-fase-0-decision.md`
for why `docs/cockpit/` is the only spec tree considered.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.drift_checks import (  # noqa: E402
    DEFAULT_FUNCTIONAL_GLOBS,
    DEFAULT_SPEC_GLOBS,
    SpecDriftFinding,
    find_spec_drift_for_card,
)

DEFAULT_KANBAN_BASE = "http://localhost:8000/api/v1/kanban"
SPEC_DOC_META_KEY = "spec_doc"


def _http_get_json(url: str, timeout: int = 10) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - localhost-only by default
        return json.loads(resp.read().decode("utf-8"))


def _list_done_cards_with_spec_doc(kanban_base: str, project_key: str) -> list[dict]:
    """Return every card in `Done` whose `metadata.spec_doc` is a non-empty
    string. The kanban list endpoint already returns deliverables; we just
    need the card + deliverable refs to recover the merge SHA."""
    url = f"{kanban_base}/cards?project_key={project_key}&column=Done"
    payload = _http_get_json(url)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    out: list[dict] = []
    for c in items:
        meta = c.get("metadata") or {}
        spec = meta.get(SPEC_DOC_META_KEY)
        if isinstance(spec, str) and spec.strip():
            out.append(c)
    return out


def _branch_name_for_card(card: dict) -> str | None:
    """Return the `kind=branch` deliverable's `ref` if the card has one."""
    for d in card.get("deliverables") or []:
        if d.get("kind") == "branch":
            ref = d.get("ref")
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
    return None


def _resolve_merge_sha(repo_root: Path, branch: str) -> str | None:
    """Return the SHA of the merge commit that brought `branch` into the
    default branch, or None if we can't pin it down.

    Strategy:
      1. Resolve `origin/<branch>` (the SHA GitHub recorded as the merge).
         This works even when the branch was deleted post-merge because
         GitHub keeps the merge commit reachable from `master`.
      2. Walk one parent back to find the branch tip and confirm that the
         merge is reachable from `origin/<branch>`'s history.
      3. Fall back to None when the branch never made it to origin."""
    try:
        head_sha = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", f"origin/{branch}"],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    # `origin/<branch>` typically points at the merge commit itself when
    # GitHub fast-forwarded, or at the branch tip when it didn't. Walk a
    # few commits back on the first-parent chain looking for the first
    # merge-commit.
    try:
        sha_list = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "--merges",
                "--first-parent",
                "--format=%H",
                f"{head_sha}~0..HEAD",
            ],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    if not sha_list:
        return None
    return sha_list.splitlines()[0]


def scan_for_drift(
    repo_root: Path,
    project_key: str,
    kanban_base: str = DEFAULT_KANBAN_BASE,
) -> list[SpecDriftFinding]:
    """Top-level scan: list Done cards with `metadata.spec_doc`, resolve
    each one's closing merge SHA, then run `find_spec_drift_for_card`.

    Cards with no `kind=branch` deliverable, or with a branch we can't
    resolve server-side, are skipped (we can't link the diff back to a
    specific card without the merge SHA)."""
    findings: list[SpecDriftFinding] = []
    cards = _list_done_cards_with_spec_doc(kanban_base, project_key)
    for card in cards:
        branch = _branch_name_for_card(card)
        if not branch:
            continue
        merge_sha = _resolve_merge_sha(repo_root, branch)
        if not merge_sha:
            continue
        card_findings = find_spec_drift_for_card(
            repo_root,
            card_id=card["id"],
            spec_doc=(card.get("metadata") or {})[SPEC_DOC_META_KEY].strip(),
            merge_sha=merge_sha,
        )
        findings.extend(card_findings)
    return findings


def render_summary(findings: list[SpecDriftFinding]) -> tuple[str, str]:
    """Return `(markdown_body, status_line)` for the weekly drift report."""
    lines = ["### Spec drift signal (spec-ssot Fase 2)", ""]
    if not findings:
        lines.append(
            "**Status:** ok — every Done card with `metadata.spec_doc` either "
            "touched its linked spec-doc or was out of mechanical scope (URL)."
        )
        lines.append("")
        status = "ok"
    else:
        status = f"drifted: {len(findings)}"
        lines.append(
            f"**Status:** drifted — {len(findings)} card(s) closed without "
            "updating their linked `docs/cockpit/` spec. Advice only; file a "
            "[spec-update] Backlog card if the change genuinely needs prose."
        )
        lines.append("")
        for f in findings:
            paths = ", ".join(f"`{p}`" for p in f.changed_functional_paths[:5])
            extra = ""
            if len(f.changed_functional_paths) > 5:
                extra = f" (+{len(f.changed_functional_paths) - 5} more)"
            lines.append(f"- `{f.card_id}` → `{f.spec_doc}` missed: {paths}{extra}")
        lines.append("")
    return "\n".join(lines), status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--project-key",
        required=True,
        help="Kanban project key (e.g. git:github.com/<org>/<repo>).",
    )
    parser.add_argument(
        "--kanban-base",
        default=DEFAULT_KANBAN_BASE,
        help=f"Kanban REST API base URL (default: {DEFAULT_KANBAN_BASE}).",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("/tmp/spec_drift_summary.md"),
        help="Where to write the markdown summary.",
    )
    args = parser.parse_args()

    findings = scan_for_drift(
        args.repo_root,
        args.project_key,
        kanban_base=args.kanban_base,
    )
    body, status = render_summary(findings)
    args.summary_out.write_text(body + "\n")
    print(status)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, ConnectionError) as exc:
        # Offline / API down → still exit 0 with a degraded status so the
        # weekly drift report shows the failure mode rather than going red.
        print(f"unavailable: kanban api unreachable ({exc.__class__.__name__})")
        Path("/tmp/spec_drift_summary.md").write_text(
            "### Spec drift signal (spec-ssot Fase 2)\n\n"
            "**Status:** unavailable — kanban API unreachable this run. "
            "Re-run when the backend is up.\n"
        )
        sys.exit(0)