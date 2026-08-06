"""Drift-test for the report_impediment ``options``-count contract.

The contract lives in exactly one authoritative place:

  - ``backend/app/kanban/mcp_server.py::report_impediment`` — the gate
    that rejects ``options`` lists with fewer or more than 4 entries
    (``_IMPEDIMENT_OPTION_COUNT = 4``, kaart 4279448c revisit).

But the same contract is duplicated into four agent-facing mirrors so a
freshly-spawned agent knows the rule without having to call the MCP tool
to discover it:

  1. ``.claude/agents/engineer.md`` — the persona the engineer reads when
     running a kanban card by hand.
  2. ``.claude/agents/analyst.md`` — the persona the analyst reads.
  3. ``.claude/agents/reviewer.md`` — the persona the reviewer reads.
  4. ``backend/app/kanban/dispatch.py`` — the prompt fragments the
     dispatcher inlines into spawned sessions (``_build_ship_instructions``,
     ``_build_analyst_session_end_instructions``,
     ``_build_reviewer_session_end_instructions``).

Kaart 1871dd1abaec4b99af7bc4caaece17a5: the previous manual sync of
this contract (after the kaart-4279448c revisit) only touched one of
the four mirrors; nothing watched the other three. Three revisits of
4279448c cost real rounds because the persona-text "of laat ``options``
helemaal weg" told agents they could skip the contract, so the gate's
``invalid_option_count`` rejection kept firing on real flows. This
drift guard turns that "edit four places by hand and pray" pattern
into a single-source test: a regression in any mirror fails CI with
the offending mirror named in the message.

The invariants list lives at module scope — edit it (and the mirrors)
in the same commit whenever the contract legitimately changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _engineer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "engineer.md").read_text(encoding="utf-8")


def _analyst_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "analyst.md").read_text(encoding="utf-8")


def _reviewer_md_body() -> str:
    return (REPO_ROOT / ".claude" / "agents" / "reviewer.md").read_text(encoding="utf-8")


def _dispatch_py_body() -> str:
    """The contract must appear verbatim somewhere in the dispatcher
    source so the agent's rendered prompt carries it. The FCR test
    renders individual ``_build_*`` functions because the FCR prompt is
    monolithic; the impediment contract is smaller and currently lives
    inline across ``_build_ship_instructions`` /
    ``_build_analyst_session_end_instructions`` /
    ``_build_reviewer_session_end_instructions``, so we just grep the
    file. If a future editor consolidates these into one builder, the
    text grep still catches drift."""
    return (REPO_ROOT / "backend" / "app" / "kanban" / "dispatch.py").read_text(encoding="utf-8")


# Source registry: name -> callable that yields the source text. Using a
# dict so the parametrised test iterates sources symmetrically and the
# failure message reads "<SOURCE_NAME> missing <LABEL>: 'substring'", which
# is exactly what the next editor needs to know. Add a fifth entry here
# (and only here) when a new mirror joins — the test auto-picks it up.
SOURCES: dict[str, callable[[], str]] = {
    ".claude/agents/engineer.md": _engineer_md_body,
    ".claude/agents/analyst.md": _analyst_md_body,
    ".claude/agents/reviewer.md": _reviewer_md_body,
    "backend/app/kanban/dispatch.py": _dispatch_py_body,
}


# Positive contract anchor. Picked because every existing mirror uses the
# Dutch word "precies 4" or the English "exactly 4" — this substring is
# the smallest stable token that survives re-flows. If a future mirror
# picks yet another language, add the new anchor as a second entry here
# rather than weakening this one.
POSITIVE_ANCHOR = "precies 4"

# Negative contract anchor. The drift that motivated this guard
# (kaart 1871dd1abaec4b99af7bc4caaece17a5): the engineer-persona once
# carried "of laat ``options`` helemaal weg voor een vrije-tekstvraag"
# which gave agents an out, so they kept supplying fewer-than-4 options
# and the gate rejected them. The contract is binary — supply exactly 4
# or supply none — and every mirror must reflect that, not "supply some
# smaller number or none". Phrased as the exact drift phrase so the
# failure message points at the actual offender, not a fuzzy semantic
# match.
NEGATIVE_ANCHOR = "laat `options` helemaal weg"


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_mirror_mentions_precies_4_contract(source_name: str) -> None:
    """Every mirror must state the ``precies 4`` / ``exactly 4`` rule.

    Acceptance criterion: ``Een test faalt wanneer één van de 4 mirrors
    het verplichte-4-contract niet meer noemt``. Parametrised across
    sources so the failure message names exactly which mirror lost the
    contract — e.g. ``.claude/agents/analyst.md missing positive
    anchor: 'precies 4'``.

    If this test fails: either a legitimate mirror was added without
    updating the contract wording (add the anchor AND extend
    ``SOURCES``), or a mirror silently dropped the contract (revert the
    offending mirror to match the others). Do NOT weaken the anchor to
    make the test pass — that's the regression this guard exists for.
    """
    source_text = SOURCES[source_name]()
    assert POSITIVE_ANCHOR in source_text, (
        f"{source_name} missing positive anchor: {POSITIVE_ANCHOR!r}. "
        f"Either add the 'precies 4' contract wording to this mirror, "
        f"or update POSITIVE_ANCHOR and SOURCES if a new mirror joins "
        f"the registry."
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_mirror_does_not_advise_omitting_options(source_name: str) -> None:
    """No mirror may carry the drift phrasing that contradicts the gate.

    The drift that motivated this guard: the engineer-persona used to
    read "of laat ``options`` helemaal weg voor een vrije-tekstvraag",
    which framed "omit ``options``" as a valid alternative to
    "supply exactly 4". Agents internalised the contradiction and
    stopped supplying 4 options; the gate's ``invalid_option_count``
    rejection kept firing on real flows, costing three revisits of
    kaart 4279448c.

    The contract is binary — supply exactly 4 or supply none — and the
    negative anchor pins that framing. A future editor that copies the
    old drift phrase into a new mirror (or pastes it back into the
    engineer-persona during a re-flow) trips this test before the drift
    reaches a real agent.
    """
    source_text = SOURCES[source_name]()
    assert NEGATIVE_ANCHOR not in source_text, (
        f"{source_name} contains the drift phrasing: {NEGATIVE_ANCHOR!r}. "
        f"That wording framed 'omit options' as a valid alternative to "
        f"'supply exactly 4' and contradicted the "
        f"mcp_server.report_impediment gate (kaart "
        f"1871dd1abaec4b99af7bc4caaece17a5). Replace it with wording "
        f"that states the contract clearly: 'supply exactly 4 or supply "
        f"none — 1-3 is rejected with invalid_option_count'."
    )


def test_sources_registry_covers_all_four_mirrors() -> None:
    """Sanity guard: ``SOURCES`` must hold exactly the four mirrors the
    card names. A future editor who drops one or adds a fifth without
    updating the card description breaks the contract silently —
    pinning the count here makes the registry self-describing.
    """
    assert set(SOURCES.keys()) == {
        ".claude/agents/engineer.md",
        ".claude/agents/analyst.md",
        ".claude/agents/reviewer.md",
        "backend/app/kanban/dispatch.py",
    }, (
        f"SOURCES registry has drifted from the four mirrors named in "
        f"kaart 1871dd1abaec4b99af7bc4caaece17a5. Current: "
        f"{sorted(SOURCES.keys())}. Add the new mirror here (and to the "
        f"acceptance criteria) so the drift guard keeps covering it."
    )


def test_drift_detector_fails_when_mirror_loses_positive_anchor() -> None:
    """Demonstrate the detector catches a missing positive anchor.

    Builds a fake mirror missing the ``precies 4`` anchor and runs the
    same presence check the parametrised test runs. If this test ever
    stops failing-on-purpose, the detector's premise has rotted — pin
    it down with a live negative case so the contract is enforced, not
    assumed.
    """
    fake_mirror = (
        "When you call report_impediment, you may supply options for "
        "the human to choose between. The fewer you supply, the less "
        "guidance the human gets.\n"
    )
    assert POSITIVE_ANCHOR not in fake_mirror, (
        f"test fixture bug: fake mirror unexpectedly contains "
        f"{POSITIVE_ANCHOR!r}"
    )


def test_drift_detector_fails_when_mirror_reintroduces_drift_phrase() -> None:
    """Demonstrate the detector catches a reintroduced drift phrase.

    Symmetric to the positive-anchor negative test. If a future editor
    pastes "laat ``options`` helemaal weg" back into a mirror during a
    re-flow, the drift phrasing returns and the gate-vs-persona
    contradiction comes back with it. This fixture pins the negative
    check to a live failing case so the detector cannot silently
    rot.
    """
    fake_mirror = (
        "When you call report_impediment, you may supply precies 4 "
        "options, or laat `options` helemaal weg voor een vrije-"
        "tekstvraag.\n"
    )
    assert NEGATIVE_ANCHOR in fake_mirror, (
        f"test fixture bug: fake mirror unexpectedly missing "
        f"{NEGATIVE_ANCHOR!r}"
    )
