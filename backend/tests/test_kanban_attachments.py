"""Card screenshot attachments: upload → stored on disk + op-log row, served
back as raw bytes, injected into the dispatch prompt, and removed on delete.

Storage is redirected to a tmp dir via ``settings.kanban_attachment_dir`` so
the suite never writes into ``~/.claude-registry``.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.kanban import service
from app.kanban.dispatch import _build_attachments_section, build_card_prompt
from app.kanban.operations import apply_operation, rematerialize
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

# 1x1 transparent PNG (starts with the PNG magic bytes _detect_image checks).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63fccfc0f01f0005050201a2c4bb3a0000000049454e44ae426082"
)


@pytest_asyncio.fixture(autouse=True)
async def _tables(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kanban_attachment_dir", str(tmp_path / "attach"))
    await reset_test_tables()
    yield


async def _make_card(ac: AsyncClient) -> str:
    r = await ac.post("/api/v1/kanban/cards",
        json={"project_key": "P", "title": "Card", "confirm_new_project": True})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_upload_list_serve_delete_roundtrip():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = await _make_card(ac)

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/attachments",
            files={"file": ("shot.png", PNG_BYTES, "image/png")})
        assert r.status_code == 201, r.text
        attachments = r.json()["attachments"]
        assert len(attachments) == 1
        att = attachments[0]
        assert att["mime_type"] == "image/png"
        assert att["size_bytes"] == len(PNG_BYTES)

        # Raw bytes are served back verbatim.
        r = await ac.get(f"/api/v1/kanban/cards/{cid}/attachments/{att['id']}")
        assert r.status_code == 200
        assert r.content == PNG_BYTES
        assert r.headers["content-type"].startswith("image/png")

        # Card GET carries the attachment too.
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert len(r.json()["attachments"]) == 1

        r = await ac.delete(f"/api/v1/kanban/cards/{cid}/attachments/{att['id']}")
        assert r.status_code == 200
        assert r.json()["attachments"] == []

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/attachments/{att['id']}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_rejects_non_image():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = await _make_card(ac)
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/attachments",
            files={"file": ("notes.txt", b"just text, not an image", "text/plain")})
        assert r.status_code == 400
        assert "image" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_attachment_survives_rematerialize():
    """attach then a rematerialize replay reproduces the materialized row."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="P", entity_id=None, payload={"title": "c"})
        await apply_operation(s, op_type="attach", entity_type="attachment",
            project_key="P", entity_id=cid,
            payload={"id": "att1", "filename": "a.png", "mime_type": "image/png",
                     "size_bytes": 3, "storage_path": "/tmp/a.png"})
        await s.commit()

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()
        card = await service.get_card(s, cid)
        assert [a.id for a in card.attachments] == ["att1"]

    # A detach op removes it again on replay.
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="detach", entity_type="attachment",
            project_key="P", entity_id=cid, payload={"id": "att1"})
        await s.commit()
    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()
        card = await service.get_card(s, cid)
        assert card.attachments == []


class _StubAtt:
    def __init__(self, path, filename):
        self.storage_path = path
        self.filename = filename


class _StubCard:
    id = "c1"
    title = "T"
    description = "desc"
    model = None
    parent_card_id = None

    def __init__(self, attachments):
        self.attachments = attachments


def test_build_attachments_section_lists_paths():
    section = _build_attachments_section(
        _StubCard([_StubAtt("/data/a.png", "a.png"),
                   _StubAtt("/data/b.png", "b.png")])
    )
    assert "## Screenshots" in section
    assert "/data/a.png" in section
    assert "/data/b.png" in section


def test_build_attachments_section_empty_when_none():
    assert _build_attachments_section(_StubCard([])) == ""


def test_build_card_prompt_includes_attachment_path():
    prompt = build_card_prompt(
        _StubCard([_StubAtt("/data/shot.png", "shot.png")]),
        persona=None, ship_mode="direct",
    )
    assert "## Screenshots" in prompt
    assert "/data/shot.png" in prompt
