"""Service for aggregating skill invocation stats from JSONL session files."""
import json
from collections import defaultdict
from pathlib import Path

import aiofiles

from app.models.schemas import SkillUsageStat
from app.utils.path_utils import get_claude_projects_dir, convert_path_to_folder_name


class SkillStatsService:
    """Scans Claude session JSONL files and counts Skill tool invocations."""

    @classmethod
    async def scan_project(cls, project_path: str) -> list[SkillUsageStat]:
        """Return skill invocation counts for the given project, sorted descending."""
        folder = convert_path_to_folder_name(project_path)
        project_dir = get_claude_projects_dir() / folder

        counts: dict[str, int] = defaultdict(int)
        if project_dir.is_dir():
            for jsonl_file in project_dir.glob("*.jsonl"):
                await cls._parse_file(jsonl_file, counts)

        return sorted(
            [SkillUsageStat(skill=k, count=v) for k, v in counts.items()],
            key=lambda s: s.count,
            reverse=True,
        )

    @staticmethod
    async def _parse_file(path: Path, counts: dict[str, int]) -> None:
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    for item in obj.get("message", {}).get("content", []):
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "tool_use"
                            and item.get("name") == "Skill"
                        ):
                            skill_name = item.get("input", {}).get("skill", "")
                            if skill_name:
                                counts[skill_name] += 1
        except Exception:
            pass
