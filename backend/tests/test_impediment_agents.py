"""Vestigial roles (developer / tester / testing / code-review) must not appear
in the impediment-routing map — only analyst and engineer exist today. Locked
in by tests so a re-introduction fails loudly instead of silently widening the
map again. See docs/cockpit/work-type-routing-analysis.md §5.3 for context.
"""
from app.api.v1.kanban import router as kanban_router


def test_impediment_agents_only_lists_real_agents():
    keys = set(kanban_router._IMPEDIMENT_AGENTS.keys())
    assert keys == {"analyst", "engineer"}, (
        f"_IMPEDIMENT_AGENTS must only contain real .claude/agents roles; got {keys}"
    )


def test_impediment_agents_targets_are_real_agents():
    allowed = {"analyst", "engineer"}
    for src, targets in kanban_router._IMPEDIMENT_AGENTS.items():
        for tgt in targets:
            assert tgt in allowed, (
                f"target {tgt!r} from {src!r} is not a real agent"
            )


def test_impediment_agents_excludes_vestigial_roles():
    vestigial = {"developer", "tester", "testing", "code-review"}
    for src, targets in kanban_router._IMPEDIMENT_AGENTS.items():
        assert src not in vestigial, f"vestigial role {src!r} still a key"
        assert not (set(targets) & vestigial), (
            f"vestigial role(s) {set(targets) & vestigial} still targeted from {src!r}"
        )
