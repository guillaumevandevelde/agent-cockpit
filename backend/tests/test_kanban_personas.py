# backend/tests/test_kanban_personas.py
from app.kanban import dispatch


def test_strip_frontmatter_removes_yaml_block():
    body = dispatch._strip_frontmatter("---\nname: x\n---\nHello\nWorld\n")
    assert body == "Hello\nWorld\n"


def test_strip_frontmatter_passthrough_when_absent():
    assert dispatch._strip_frontmatter("Just text") == "Just text"


def test_persona_filename_for_column():
    assert dispatch._persona_filename("Analysis") == "analyst.md"
    assert dispatch._persona_filename("Todo") == "developer.md"
    assert dispatch._persona_filename("Backlog") is None


def test_read_persona_returns_body(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.md").write_text("---\nname: dev\n---\nBe a developer.\n")
    assert dispatch._read_persona(str(tmp_path), "Todo") == "Be a developer."


def test_read_persona_missing_file_returns_none(tmp_path):
    assert dispatch._read_persona(str(tmp_path), "Todo") is None
