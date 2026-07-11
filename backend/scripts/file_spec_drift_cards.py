"""File `[spec-update]` Backlog cards for every drift finding produced by
`check_spec_drift.py`.

Runs as a separate human- (or scheduled-) triggered script (NOT inside the
weekly CI cron, because the kanban REST API requires a running backend and
the workflow currently has no credentials to mutate the board). The drift
summary that `check_spec_drift.py` writes to `/tmp/spec_drift_summary.md`
is the trigger input — this script reads it back, extracts the per-card
drift lines, and POSTs one Backlog card per finding.

Why a separate script (and not a flag on `check_spec_drift.py`):
  * CI stays signal-only (no kanban mutation, no auth surface).
  * The card-creation step can be re-run idempotently without re-running
    the (slower, git-walking) detection step.
  * Operators can dry-run the card body before filing by passing
    `--dry-run` and inspecting the JSON output.

Idempotency: each card's title is namespaced with the source card id
(`[spec-update] c1 → docs/cockpit/foo.md`). Listing the Backlog and
filtering on that prefix before posting skips already-filed drifts. The
caller is expected to invoke `--prune-existing` to skip duplicates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_KANBAN_BASE = "http://localhost:8000/api/v1/kanban"

# Matches the per-card lines emitted by check_spec_drift.py.render_summary,
# e.g. "`c1` → `docs/cockpit/foo.md` missed: `backend/app/main.py` (+2 more)".
_DRIFT_LINE_RE = re.compile(
    r"^- `(?P<card_id>[^`]+)` → `(?P<spec_doc>[^`]+)` "
    r"missed: (?P<rest>.*)$",
    re.MULTILINE,
)


def parse_summary(summary_path: Path) -> list[dict[str, str]]:
    """Extract per-card drift lines from a markdown summary."""
    text = summary_path.read_text()
    findings: list[dict[str, str]] = []
    for m in _DRIFT_LINE_RE.finditer(text):
        findings.append({
            "card_id": m.group("card_id"),
            "spec_doc": m.group("spec_doc"),
            "rest": m.group("rest"),
        })
    return findings


def card_title(card_id: str, spec_doc: str) -> str:
    return f"[spec-update] {card_id} → {spec_doc}"


def card_description(card_id: str, spec_doc: str, rest: str) -> str:
    return (
        "## What this is\n"
        "Advice-only card filed by `backend/scripts/file_spec_drift_cards.py` "
        "after `check_spec_drift.py` flagged probable spec drift. The linked "
        f"`{spec_doc}` was not updated alongside functional changes that "
        f"landed in card `{card_id}`. Review and either update the spec-doc "
        "or close this card with a justification.\n\n"
        "## Functional paths the closing merge touched\n"
        f"{rest}\n\n"
        "## Why this is signal, not a gate\n"
        "Per `docs/cockpit/spec-driven-development-analysis.md` §4-5, prose ↔ "
        "code correspondence can only be signalled, not enforced mechanically. "
        "The drift report (`.github/workflows/drift-report.yml`) surfaces this "
        "weekly; this card is the actionable follow-up.\n\n"
        "## Acceptance criteria\n"
        "- [ ] Linked spec-doc reviewed against the functional change\n"
        "- [ ] Either updated to reflect the change, OR comment posted with "
        "why no update was needed\n"
        "- [ ] Card moved to Done\n"
    )


def _http_get(url: str, timeout: int = 10) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, body: dict, timeout: int = 10) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def list_backlog_titles(kanban_base: str, project_key: str) -> set[str]:
    payload = _http_get(f"{kanban_base}/cards?project_key={project_key}&column=Backlog")
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return {item["title"] for item in items}


def file_cards(
    kanban_base: str,
    project_key: str,
    findings: list[dict[str, str]],
    *,
    prune_existing: bool = True,
    dry_run: bool = False,
) -> list[dict]:
    """POST one card per finding to the kanban Backlog column.

    Returns the list of `{"card_id": ..., "title": ..., "status": ...}`
    entries — `status` is one of `"filed"`, `"skipped_existing"`, `"dry_run"`."""
    existing: set[str] = set()
    if prune_existing and not dry_run:
        existing = list_backlog_titles(kanban_base, project_key)

    results: list[dict] = []
    for f in findings:
        title = card_title(f["card_id"], f["spec_doc"])
        if title in existing:
            results.append({"card_id": None, "title": title, "status": "skipped_existing"})
            continue
        body = {
            "project_key": project_key,
            "title": title,
            "description": card_description(f["card_id"], f["spec_doc"], f["rest"]),
            "column": "Backlog",
            "labels": ["spec-drift", "spec-ssot"],
            "work_type": "chore",
            "agent": "engineer",
            "metadata": {
                "spec_doc": f["spec_doc"],
                "drift_source_card_id": f["card_id"],
            },
        }
        if dry_run:
            results.append({"card_id": None, "title": title, "status": "dry_run", "body": body})
            continue
        try:
            resp = _http_post(f"{kanban_base}/cards", body)
        except urllib.error.HTTPError as exc:  # pragma: no cover - depends on backend
            results.append({
                "card_id": None,
                "title": title,
                "status": f"error_{exc.code}",
            })
            continue
        results.append({
            "card_id": resp.get("id"),
            "title": title,
            "status": "filed",
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
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
        "--summary-in",
        type=Path,
        default=Path("/tmp/spec_drift_summary.md"),
        help="Markdown summary produced by check_spec_drift.py.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Skip the existing-title check and file every drift finding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the card bodies that would be filed, do not POST.",
    )
    args = parser.parse_args()

    findings = parse_summary(args.summary_in)
    if not findings:
        print("no drift findings in summary; nothing to file")
        return 0

    results = file_cards(
        args.kanban_base,
        args.project_key,
        findings,
        prune_existing=not args.no_prune,
        dry_run=args.dry_run,
    )
    for r in results:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())