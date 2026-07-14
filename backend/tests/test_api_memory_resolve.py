"""API tests for /api/v1/memory/rules/resolve — path/keyword trigger resolution."""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _write_rule(rules_dir: Path, name: str, body: str, frontmatter: str | None = None) -> Path:
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{name}.md"
    if frontmatter:
        path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    else:
        path.write_text(f"{body}\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_matched_and_unmatched(tmp_path):
    project_path = str(tmp_path)
    rules_dir = tmp_path / ".claude" / "rules"
    _write_rule(
        rules_dir,
        "deploy",
        "Run deploy carefully.",
        frontmatter="keywords:\n  - deploy\n",
    )
    _write_rule(rules_dir, "always-on", "Be concise.")

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/memory/rules/resolve",
            params={"project_path": project_path},
            json={"prompt": "please deploy", "touched_files": []},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    matched = body["matched_rules"]
    unmatched = body["unmatched_rules"]
    matched_names = {m["name"] for m in matched}
    unmatched_names = {u["name"] for u in unmatched}
    assert "deploy" in matched_names
    assert "always-on" in matched_names
    assert matched_names.isdisjoint(unmatched_names)


@pytest.mark.asyncio
async def test_resolve_endpoint_path_trigger(tmp_path):
    project_path = str(tmp_path)
    rules_dir = tmp_path / ".claude" / "rules"
    _write_rule(
        rules_dir,
        "python-style",
        "Use async SQLAlchemy.",
        frontmatter="paths:\n  - backend/**/*.py\n",
    )

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/memory/rules/resolve",
            params={"project_path": project_path},
            json={
                "prompt": "review this",
                "touched_files": ["backend/app/services/foo.py"],
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    matched_names = {m["name"] for m in body["matched_rules"]}
    assert "python-style" in matched_names
    matched = next(m for m in body["matched_rules"] if m["name"] == "python-style")
    assert any(t.startswith("path:") for t in matched["matched_triggers"])


@pytest.mark.asyncio
async def test_resolve_endpoint_requires_project_path():
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/memory/rules/resolve",
            json={"prompt": "x", "touched_files": []},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolve_endpoint_empty_prompt_no_touched_files_returns_only_always_on(tmp_path):
    project_path = str(tmp_path)
    rules_dir = tmp_path / ".claude" / "rules"
    _write_rule(
        rules_dir,
        "scoped",
        "scoped.",
        frontmatter="paths:\n  - backend/**/*.py\nkeywords:\n  - deploy\n",
    )
    _write_rule(rules_dir, "always-on", "always.")

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/memory/rules/resolve",
            params={"project_path": project_path},
            json={"prompt": "", "touched_files": []},
        )
    assert r.status_code == 200, r.text
    matched_names = {m["name"] for m in r.json()["matched_rules"]}
    assert "always-on" in matched_names
    assert "scoped" not in matched_names