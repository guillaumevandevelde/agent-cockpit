# backend/tests/test_kanban_personas.py
from pathlib import Path

import yaml

import app.kanban.analyst_prompt as _analyst_prompt_module
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


def test_project_analyst_md_frontmatter_description_covers_both_modes():
    """The frontmatter `description:` of the shipped `.claude/agents/analyst.md`
    must mention both modi — multi-agent decompositie AND leaf
    design-deliverable. A bare 'Voert niets zelf uit' was the pre-leaf-spike
    drift that this contract prevents from re-appearing: the description is
    what an operator sees in the Cockpit agent-list UI and what
    `AgentService._scan_agents_dir` exposes via the `description` field on
    `/api/v1/agents`."""
    repo_root = Path(__file__).resolve().parents[2]
    persona_path = repo_root / ".claude" / "agents" / "analyst.md"
    text = persona_path.read_text()
    assert text.startswith("---\n"), "persona must start with YAML frontmatter"
    end = text.find("\n---\n", 4)
    assert end != -1, "persona must have a closing frontmatter delimiter"
    frontmatter = yaml.safe_load(text[4:end])
    assert isinstance(frontmatter, dict)
    description = frontmatter.get("description") or ""
    assert isinstance(description, str) and description.strip(), (
        "analyst.md frontmatter must have a non-empty `description:` field"
    )

    # Modus 1 — multi-agent decomposition must be referenced.
    desc_lower = description.lower()
    assert (
        "multi-agent" in desc_lower and "decompositie" in desc_lower
    ), (
        "analyst.md frontmatter description must mention multi-agent "
        "decompositie (modus 1). "
        f"Got description: {description!r}"
    )

    # Modus 2 — leaf design-deliverable must be referenced. Accept any of
    # the conventional spellings; the persona body uses 'Leaf
    # design-deliverable' (capitalised) but the frontmatter description
    # case may vary.
    assert (
        "leaf design-deliverable" in desc_lower
        or "leaf spike" in desc_lower
        or "design-deliverable" in desc_lower
    ), (
        "analyst.md frontmatter description must mention leaf "
        "design-deliverable (modus 2) so an operator doesn't think "
        "the analyst persona can never execute. "
        f"Got description: {description!r}"
    )


def test_analyst_prompt_module_docstring_covers_both_modes():
    """The `analyst_prompt.py` module docstring is what an operator reads in
    the IDE/docs when there's no project-local `analyst.md`. It must also
    acknowledge both modi — a bare 'planning, not implementing' framing is
    no longer accurate after the leaf-spike change, mirroring the
    frontmatter-description drift that this contract guards against."""
    doc = _analyst_prompt_module.__doc__ or ""
    assert doc.strip(), "analyst_prompt.py must have a module docstring"

    doc_lower = doc.lower()
    assert (
        "multi-agent" in doc_lower and "decompositie" in doc_lower
    ), (
        "analyst_prompt.py module docstring must mention multi-agent "
        "decompositie (modus 1). "
        f"Got docstring: {doc!r}"
    )
    assert (
        "leaf design-deliverable" in doc_lower
        or "leaf spike" in doc_lower
    ), (
        "analyst_prompt.py module docstring must mention leaf "
        "design-deliverable (modus 2). "
        f"Got docstring: {doc!r}"
    )


def test_engineer_persona_frontmatter_pins_sonnet_default():
    """Regression guard for kanban card 7c120256: the engineer persona's
    `model:` frontmatter must stay pinned to `sonnet` so the token-cost
    optimisation from token-optimization-analysis.md §4 R1 (~5x input-price
    difference vs opus) isn't silently reverted by a future persona edit."""
    repo_root = Path(__file__).resolve().parents[2]
    model = dispatch._read_persona_model(str(repo_root), "engineer.md")
    assert model == "sonnet", (
        f"engineer persona frontmatter is {model!r}, expected 'sonnet' — "
        "see docs/cockpit/token-optimization-analysis.md §4 R1"
    )


def test_analyst_persona_frontmatter_pins_opus_default():
    """Inverse guard: the analyst persona's `model:` frontmatter must stay
    pinned to `opus` — see token-optimization-analysis.md §4 R1, which keeps
    opus for analyst while sonnet-defaulting engineer."""
    repo_root = Path(__file__).resolve().parents[2]
    model = dispatch._read_persona_model(str(repo_root), "analyst.md")
    assert model == "opus", (
        f"analyst persona frontmatter is {model!r}, expected 'opus' — "
        "see docs/cockpit/token-optimization-analysis.md §4 R1"
    )


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


# ---- Modus-2 content that used to live only in the dispatch-injected -----
# ---- override (removed in kanban card fbe7937e99484941b196bf2ebc0866f6) --
# The persona is now the single source of truth for the leaf design-
# deliverable contract: the follow-up cards clause (with its Backlog-spam
# guards), the scoped impediment-escape, and the outcome enum. These tests
# replace the dispatch-level assertions that used to pin this text inside
# `_analyst_leaf_spike_override_note()`.


def _analyst_md_body() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    persona_path = repo_root / ".claude" / "agents" / "analyst.md"
    return dispatch._strip_frontmatter(persona_path.read_text())


def test_analyst_md_follow_up_cards_clause_relaxes_create_card():
    body = _analyst_md_body()
    assert "create_card" in body and "add_plan_attachment" in body
    body_lower = body.lower()
    assert any(
        marker in body_lower
        for marker in ("relaxed", "relax", "toegestaan", "permitted", "allowed")
    ), "analyst.md must explicitly relax create_card/add_plan_attachment for modus 2"


def test_analyst_md_follow_up_cards_clause_has_spam_guards():
    body = _analyst_md_body()
    body_lower = body.lower()
    assert "acceptance criteria" in body_lower or "acceptance-criteria" in body_lower
    assert "list_cards" in body
    assert "depends_on" in body


def test_analyst_md_has_scoped_impediment_escape():
    body = _analyst_md_body()
    body_lower = body.lower()
    assert "report_impediment" in body
    assert "best-effort" in body_lower or "best effort" in body_lower
    assert "conditio" in body_lower  # matches "conditional" / "conditionele"


def test_analyst_md_outcome_contract_names_enum_and_field():
    body = _analyst_md_body()
    for outcome in ("decomposed", "not_feasible", "no_action_needed"):
        assert outcome in body, f"analyst.md must name outcome enum value {outcome!r}"
    assert "outcome" in body.lower()
    assert "move_card" in body
    assert "Done" in body


def test_analyst_md_no_longer_references_dispatch_override():
    """The dispatch layer no longer injects an override note above the
    persona (kanban card fbe7937e99484941b196bf2ebc0866f6) — the persona
    must not point at a mechanism that no longer exists."""
    body = _analyst_md_body()
    assert "Analyst-leaf-spike override" not in body


# ---- spec_doc producer instruction (kanban card c0cccd74) ----
# The shipped analyst.md is the *effective* producer in this repo (it wins
# over the ANALYST_PROMPT fallback), so the spec_doc instruction must hold
# here too — otherwise the producer never reaches dispatched sessions.
# See docs/cockpit/spec-doc-producer-design.md §4.


def test_analyst_md_instructs_spec_doc_producer():
    body = _analyst_md_body()
    assert "spec_doc" in body
    assert "docs/cockpit" in body
    body_lower = body.lower()
    assert "implementeert" in body_lower or "implements" in body_lower


def test_analyst_md_spec_doc_respects_plan_attachment_exception():
    # The child whose spec IS the plan-attachment gets no explicit link —
    # the existing Fase-1 exception must survive.
    body_lower = _analyst_md_body().lower()
    assert "plan-attachment" in body_lower or "plan_ref" in body_lower


def test_analyst_md_requires_external_credential_preflight_guidance():
    body_lower = _analyst_md_body().lower()
    assert "externe credential" in body_lower
    assert "credential_name" in body_lower
    assert "minimax_api_key" in body_lower
    assert "get /api/v1/secrets/?project_key=" in body_lower
    assert "namen" in body_lower and "waarden" in body_lower
