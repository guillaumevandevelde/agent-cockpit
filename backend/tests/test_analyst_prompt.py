from app.kanban import analyst_prompt as _analyst_prompt_module
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


# ---- Leaf design-deliverable contract (kanban card c2b478ca) ----
# The analyst persona is loaded for any `work_type='analysis'` card, but only
# some of those are multi-agent decompositions; the rest are leaf
# design-deliverables that must write a doc, commit, ship, and move the card
# to Done. The persona itself must acknowledge both modes so a fresh session
# doesn't see a contradiction between "Verboden: geen Write/Edit" and the
# executor ship workflow injected at the bottom of the prompt. See kanban
# card c2b478ca396a473287aa0c04a79890e2 for the full report.


def test_prompt_explicitly_covers_multi_agent_decomposition_mode():
    assert "Multi-agent decompositie" in ANALYST_PROMPT
    assert "analyst_agent_id" in ANALYST_PROMPT


def test_prompt_explicitly_covers_leaf_design_deliverable_mode():
    assert "Leaf design-deliverable" in ANALYST_PROMPT or "leaf spike" in ANALYST_PROMPT
    # The leaf mode must mention writing the deliverable and shipping —
    # otherwise the prohibition below still reads as a contradiction.
    assert "schrijf" in ANALYST_PROMPT.lower() or "Write" in ANALYST_PROMPT
    assert "commit" in ANALYST_PROMPT.lower() or "ship" in ANALYST_PROMPT.lower()


def test_prompt_verboden_scoped_to_decomposition_mode():
    """The 'Verboden' section header must be qualified so the prohibitions
    clearly apply only to the multi-agent decomposition path. A fresh
    session on a `[design]`-deliverable-kaart would otherwise see a flat
    prohibition that contradicts the executor ship workflow injected at
    the bottom of the prompt."""
    # Find the actual section header (`Verboden ...` at the start of a line),
    # not any inline backtick mention in the intro.
    import re
    matches = list(re.finditer(r"(?m)^Verboden[^\n]*", ANALYST_PROMPT))
    assert matches, "ANALYST_PROMPT must have a 'Verboden' section header"
    header_line = matches[-1].group(0)
    assert "modus 1" in header_line or "decompositie" in header_line.lower(), (
        "Verboden section header must be qualified with 'modus 1' or "
        "'decompositie' so the prohibition is clearly scoped to the "
        "multi-agent decomposition path, not a blanket 'no Write/Edit' "
        f"rule. Got header: {header_line!r}"
    )


# ---- Module-docstring frontmatter contract ----
# The `analyst_prompt.py` module docstring is the fallback persona's
# "frontmatter" from an operator's perspective — it's what someone reads in
# the IDE or docs when there's no project-local `analyst.md`. After the
# leaf-spike change both modi are real, so the docstring must explicitly
# name them — a bare "planning, not implementing" framing re-introduces the
# same kind of contradiction the leaf-spike was added to fix. See kanban
# card c2b478ca396a473287aa0c04a79890e2 for the original two-modi framing.


def test_analyst_prompt_module_docstring_covers_both_modes():
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
        "design-deliverable (modus 2) so an operator reading the docstring "
        "doesn't conclude the analyst persona can never execute. "
        f"Got docstring: {doc!r}"
    )
