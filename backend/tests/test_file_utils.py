import json

import pytest

from app.utils.file_utils import write_json_file, write_text_file


@pytest.mark.asyncio
async def test_atomic_json_write_replaces_content(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{"old": true}\n', encoding="utf-8")

    assert await write_json_file(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_atomic_text_write_creates_parent(tmp_path):
    target = tmp_path / "nested" / "memory.md"

    assert await write_text_file(target, "# Memory\n")

    assert target.read_text(encoding="utf-8") == "# Memory\n"
