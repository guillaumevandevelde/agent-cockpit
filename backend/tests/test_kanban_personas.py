# backend/tests/test_kanban_personas.py
from pathlib import Path

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


def test_read_persona_model_returns_frontmatter_model(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'claude-opus-4-8'\n---\nBe an engineer.\n"
    )
    assert dispatch._read_persona_model(str(tmp_path), "engineer.md") == "claude-opus-4-8"


def test_read_persona_model_returns_none_when_field_absent(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("---\nname: 'analyst'\n---\nBe an analyst.\n")
    assert dispatch._read_persona_model(str(tmp_path), "analyst.md") is None


def test_read_persona_model_returns_none_when_no_frontmatter(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "plain.md").write_text("Just a body, no frontmatter.\n")
    assert dispatch._read_persona_model(str(tmp_path), "plain.md") is None


def test_read_persona_model_returns_none_for_missing_file(tmp_path):
    assert dispatch._read_persona_model(str(tmp_path), "missing.md") is None


def test_read_persona_model_returns_none_for_malformed_yaml(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "broken.md").write_text("---\nmodel: [unclosed\n---\nBody.\n")
    assert dispatch._read_persona_model(str(tmp_path), "broken.md") is None


# ---- Leaf design-deliverable contract on the project-local analyst.md ----
# The same contract that the fallback `ANALYST_PROMPT` must satisfy also has
# to hold for the project-local `.claude/agents/analyst.md` — a project owner
# who customises the persona must not silently re-introduce the contradiction
# that the leaf-spike runtime override was added to patch. See kanban card
# c2b478ca396a473287aa0c04a79890e2.


def test_project_analyst_md_covers_both_modes(tmp_path):
    """The shipped .claude/agents/analyst.md must explicitly distinguish
    multi-agent decomposition (prohibitions apply) from leaf
    design-deliverable (write+ship), so a fresh session reading the
    persona doesn't see a flat contradiction with the executor ship
    workflow injected at the bottom of the prompt."""
    # Resolve from this repo's working copy, not the worktree's tmp_path.
    repo_root = Path(__file__).resolve().parents[2]
    persona_path = repo_root / ".claude" / "agents" / "analyst.md"
    body = dispatch._strip_frontmatter(persona_path.read_text())

    assert "Multi-agent" in body and "decompositie" in body, (
        "analyst.md must explicitly describe the multi-agent decomposition "
        "mode so a session knows when the prohibitions apply."
    )
    # The leaf mode must be explicitly named (one of: leaf design-deliverable,
    # leaf spike, or a 'design-deliverable' phrase). Strip the body's
    # `decompositie` window first to avoid a trivial substring match.
    assert (
        "Leaf design-deliverable" in body
        or "leaf design-deliverable" in body
        or "leaf spike" in body.lower()
    ), "analyst.md must explicitly describe the leaf design-deliverable mode"

    # The Verboden section header must be scoped to the decomposition mode —
    # a flat "Verboden: geen Write/Edit" without that scope is the
    # contradiction the card reports. Find the section header line
    # (`## Verboden ...`) — not an inline backtick mention.
    import re
    matches = list(re.finditer(r"(?m)^##\s+Verboden[^\n]*", body))
    assert matches, "analyst.md must have a '## Verboden' section header"
    header_line = matches[-1].group(0)
    assert "modus 1" in header_line or "decompositie" in header_line.lower(), (
        "analyst.md: Verboden section header must be qualified with 'modus 1' "
        "or 'decompositie' so the prohibition is clearly scoped to the "
        "multi-agent decomposition path. "
        f"Got header: {header_line!r}"
    )
