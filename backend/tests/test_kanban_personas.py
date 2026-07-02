# backend/tests/test_kanban_personas.py
from app.kanban import dispatch


def test_strip_frontmatter_removes_yaml_block():
    body = dispatch._strip_frontmatter("---\nname: x\n---\nHello\nWorld\n")
    assert body == "Hello\nWorld\n"


def test_strip_frontmatter_passthrough_when_absent():
    assert dispatch._strip_frontmatter("Just text") == "Just text"


def test_persona_filename_for_column():
    # Fixed columns have no persona
    assert dispatch._persona_filename("Backlog") is None
    assert dispatch._persona_filename("Impediment") is None
    assert dispatch._persona_filename("Done") is None
    # Agent columns use the column name as persona filename
    assert dispatch._persona_filename("analyst") == "analyst.md"
    assert dispatch._persona_filename("developer") == "developer.md"
    assert dispatch._persona_filename("testing") == "testing.md"


def test_read_persona_returns_body(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "developer.md").write_text("---\nname: dev\n---\nBe a developer.\n")
    assert dispatch._read_persona(str(tmp_path), "developer") == "Be a developer."


def test_read_persona_missing_file_returns_none(tmp_path):
    assert dispatch._read_persona(str(tmp_path), "Todo") is None
