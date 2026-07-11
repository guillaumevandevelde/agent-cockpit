"""Tests for the card-filing companion to check_spec_drift.py.

The companion script reads the markdown summary that check_spec_drift.py
emits and POSTs one `[spec-update]` Backlog card per finding. It is
intentionally lightweight — the heavy lifting (git walks, path matching)
lives in scripts.drift_checks and is covered by tests/test_drift_checks.py.
What we verify here is:

  * parse_summary pulls every drift line out of a real summary body.
  * card_title / card_description produce the expected, parseable shape.
  * list_backlog_titles + file_cards correctly skip duplicates and POST
    fresh ones, using a fake HTTP layer (no real kanban backend).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.file_spec_drift_cards import (
    card_description,
    card_title,
    file_cards,
    list_backlog_titles,
    parse_summary,
)

SAMPLE_SUMMARY = """### Spec drift signal (spec-ssot Fase 2)

**Status:** drifted — 2 card(s) closed without updating their linked `docs/cockpit/` spec.

- `c1` → `docs/cockpit/foo.md` missed: `backend/app/main.py`, `frontend/src/features/x/foo.tsx`
- `c2` → `docs/cockpit/bar.md` missed: `backend/app/svc.py` (+2 more)
"""


def test_parse_summary_returns_one_entry_per_drift_line(tmp_path: Path):
    summary = tmp_path / "summary.md"
    summary.write_text(SAMPLE_SUMMARY)

    findings = parse_summary(summary)

    assert len(findings) == 2
    assert findings[0]["card_id"] == "c1"
    assert findings[0]["spec_doc"] == "docs/cockpit/foo.md"
    assert "backend/app/main.py" in findings[0]["rest"]
    assert findings[1]["card_id"] == "c2"
    assert findings[1]["spec_doc"] == "docs/cockpit/bar.md"
    assert "(+2 more)" in findings[1]["rest"]


def test_parse_summary_empty_when_no_drift_lines(tmp_path: Path):
    summary = tmp_path / "summary.md"
    summary.write_text("### Spec drift signal (spec-ssot Fase 2)\n\n**Status:** ok\n")

    assert parse_summary(summary) == []


def test_card_title_namespaces_per_source_card():
    assert card_title("c1", "docs/cockpit/foo.md") == "[spec-update] c1 → docs/cockpit/foo.md"


def test_card_description_includes_card_id_spec_doc_and_paths():
    desc = card_description("c1", "docs/cockpit/foo.md", "`backend/app/main.py`")

    assert "spec-driven-development-analysis.md" in desc
    assert "`docs/cockpit/foo.md`" in desc
    assert "c1" in desc
    assert "backend/app/main.py" in desc


def test_list_backlog_titles_returns_titles_only():
    fake_payload = {"items": [
        {"id": "a", "title": "[spec-update] c1 → docs/cockpit/foo.md"},
        {"id": "b", "title": "Some unrelated card"},
    ]}
    with patch("scripts.file_spec_drift_cards._http_get", return_value=fake_payload):
        titles = list_backlog_titles("http://x/api/v1/kanban", "git:github.com/foo/bar")

    assert titles == {
        "[spec-update] c1 → docs/cockpit/foo.md",
        "Some unrelated card",
    }


def test_file_cards_skips_existing_titles():
    findings = [
        {"card_id": "c1", "spec_doc": "docs/cockpit/foo.md", "rest": "`backend/app/main.py`"},
        {"card_id": "c2", "spec_doc": "docs/cockpit/bar.md", "rest": "`backend/app/svc.py`"},
    ]
    existing = {
        "[spec-update] c1 → docs/cockpit/foo.md",  # already filed
    }
    posted: list[dict] = []

    def fake_post(url: str, body: dict, timeout: int = 10):
        posted.append(body)
        return {"id": f"new-{len(posted)}"}

    with (
        patch("scripts.file_spec_drift_cards.list_backlog_titles", return_value=existing),
        patch("scripts.file_spec_drift_cards._http_post", side_effect=fake_post),
    ):
        results = file_cards("http://x/api/v1/kanban", "git:github.com/foo/bar", findings)

    assert [r["status"] for r in results] == ["skipped_existing", "filed"]
    assert len(posted) == 1
    assert posted[0]["title"] == "[spec-update] c2 → docs/cockpit/bar.md"
    assert posted[0]["column"] == "Backlog"
    assert posted[0]["work_type"] == "chore"
    assert posted[0]["agent"] == "engineer"
    assert posted[0]["metadata"]["spec_doc"] == "docs/cockpit/bar.md"
    assert posted[0]["metadata"]["drift_source_card_id"] == "c2"


def test_file_cards_dry_run_returns_bodies_without_posting():
    findings = [
        {"card_id": "c1", "spec_doc": "docs/cockpit/foo.md", "rest": "`backend/app/main.py`"},
    ]

    results = file_cards(
        "http://x/api/v1/kanban", "git:github.com/foo/bar", findings, dry_run=True,
    )

    assert len(results) == 1
    assert results[0]["status"] == "dry_run"
    assert results[0]["title"] == "[spec-update] c1 → docs/cockpit/foo.md"
    assert "body" in results[0]
    assert results[0]["body"]["column"] == "Backlog"


def test_file_cards_prune_disabled_files_every_finding():
    findings = [
        {"card_id": "c1", "spec_doc": "docs/cockpit/foo.md", "rest": "`a.py`"},
        {"card_id": "c2", "spec_doc": "docs/cockpit/bar.md", "rest": "`b.py`"},
    ]
    posted: list[dict] = []

    def fake_post(url: str, body: dict, timeout: int = 10):
        posted.append(body)
        return {"id": f"new-{len(posted)}"}

    # With prune_existing=False, list_backlog_titles must NOT be called;
    # patch it to raise so any accidental call fails the test loudly.
    with (
        patch(
            "scripts.file_spec_drift_cards.list_backlog_titles",
            side_effect=AssertionError("should not be called when prune disabled"),
        ),
        patch("scripts.file_spec_drift_cards._http_post", side_effect=fake_post),
    ):
        results = file_cards(
            "http://x/api/v1/kanban", "git:github.com/foo/bar", findings,
            prune_existing=False,
        )

    assert [r["status"] for r in results] == ["filed", "filed"]
    assert len(posted) == 2
    assert json.dumps(posted)  # body is serializable
