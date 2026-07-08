from app.kanban.analyst_prompt import ANALYST_PROMPT


def test_prompt_has_werkwijze_section():
    assert "Werkwijze" in ANALYST_PROMPT


def test_prompt_has_verboden_section():
    assert "Verboden" in ANALYST_PROMPT


def test_prompt_lists_required_tools():
    for tool in ("mcp__cockpit-kanban__create_card",
                 "mcp__cockpit-kanban__add_plan_attachment",
                 "mcp__cockpit-kanban__move_card"):
        assert tool in ANALYST_PROMPT


def test_prompt_instructs_parent_to_done():
    assert "Done" in ANALYST_PROMPT
