"""Tests for SkillStatsService."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.services.skill_stats_service import SkillStatsService


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _assistant_with_skill(skill_name: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": "s1",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": skill_name}}
            ]
        },
    }


def _assistant_with_bash() -> dict:
    return {
        "type": "assistant",
        "sessionId": "s1",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
            ]
        },
    }


def _make_project_dir(tmp_path: Path) -> tuple[Path, str]:
    project_dir = tmp_path / "projects" / "-fake-project"
    project_dir.mkdir(parents=True)
    return project_dir, "/fake/project"


def _patch_paths(tmp_path: Path):
    return (
        patch("app.services.skill_stats_service.get_claude_projects_dir", return_value=tmp_path / "projects"),
        patch("app.services.skill_stats_service.convert_path_to_folder_name", return_value="-fake-project"),
    )


@pytest.mark.asyncio
async def test_scan_empty_directory(tmp_path):
    _, project_path = _make_project_dir(tmp_path)
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert stats == []


@pytest.mark.asyncio
async def test_scan_single_session(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    _write_jsonl(project_dir / "abc.jsonl", [
        _assistant_with_skill("brainstorming"),
        _assistant_with_skill("brainstorming"),
        _assistant_with_skill("tdd"),
    ])
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert len(stats) == 2
    assert stats[0].skill == "brainstorming"
    assert stats[0].count == 2
    assert stats[1].skill == "tdd"
    assert stats[1].count == 1


@pytest.mark.asyncio
async def test_scan_multiple_sessions(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    _write_jsonl(project_dir / "s1.jsonl", [_assistant_with_skill("brainstorming")])
    _write_jsonl(project_dir / "s2.jsonl", [
        _assistant_with_skill("brainstorming"),
        _assistant_with_skill("tdd"),
    ])
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    by_name = {s.skill: s.count for s in stats}
    assert by_name["brainstorming"] == 2
    assert by_name["tdd"] == 1


@pytest.mark.asyncio
async def test_non_skill_tool_use_ignored(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    _write_jsonl(project_dir / "s.jsonl", [
        _assistant_with_bash(),
        _assistant_with_skill("brainstorming"),
    ])
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert len(stats) == 1
    assert stats[0].skill == "brainstorming"


@pytest.mark.asyncio
async def test_non_assistant_records_ignored(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    _write_jsonl(project_dir / "s.jsonl", [
        {"type": "user", "message": {"content": "hello"}},
        _assistant_with_skill("brainstorming"),
    ])
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert stats[0].count == 1


@pytest.mark.asyncio
async def test_sorted_descending(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    _write_jsonl(project_dir / "s.jsonl", [
        _assistant_with_skill("rare"),
        _assistant_with_skill("common"),
        _assistant_with_skill("common"),
        _assistant_with_skill("common"),
    ])
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert stats[0].skill == "common"
    assert stats[1].skill == "rare"


@pytest.mark.asyncio
async def test_malformed_lines_skipped(tmp_path):
    project_dir, project_path = _make_project_dir(tmp_path)
    path = project_dir / "s.jsonl"
    path.write_text(
        "not-json\n" + json.dumps(_assistant_with_skill("brainstorming")),
        encoding="utf-8",
    )
    p1, p2 = _patch_paths(tmp_path)
    with p1, p2:
        stats = await SkillStatsService.scan_project(project_path)
    assert stats[0].count == 1
