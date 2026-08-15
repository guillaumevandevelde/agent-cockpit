# backend/tests/test_kanban_dispatch.py
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.kanban import dispatch, service
from app.kanban.dispatch import MemoryLimitExceeded
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.fixture(autouse=True)
def _empty_plugin_registry(tmp_path, monkeypatch):
    """Pin ``installed_plugins.json`` to an empty registry so the
    context-mode merge path (kanban card ``[self-improve] context-mode-plugin
    blokkeert WebFetch en curl``) does NOT fire in these tests.

    These tests pin the strict-isolation contract — the project ``.mcp.json``
    is what lands in ``--mcp-config``, no merged copy. The merge itself has
    its own tests in ``test_runs_cc_spawn_context_mode.py`` with explicit
    plugin fixtures. Keeping this file's fixture empty by default means a
    stray host plugin (e.g. context-mode installed on the test machine)
    can't silently turn the helper's ``--mcp-config`` argument into a
    merged copy and trip an exact-path assertion.
    """
    from app.utils import path_utils

    registry = tmp_path / "installed_plugins.json"
    registry.write_text('{"version": 2, "plugins": {}}', encoding="utf-8")
    monkeypatch.setattr(path_utils, "get_installed_plugins_file", lambda: registry)


PK = "git:example.com/me/repo"


async def _make_card(s, title="Task", column="Backlog", priority=None, scheduled_at=None):
    payload = {"title": title, "column": column}
    if priority is not None:
        payload["priority"] = priority
    if scheduled_at is not None:
        payload["scheduled_at"] = scheduled_at
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload=payload,
    )
    await s.flush()
    return cid


class RecordingTransport:
    """A real (non-mock) transport that records calls and returns a session."""
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None,
                 endpoint_name=None, endpoint_base_url=None,
                 endpoint_auth_token=None,
                 card_id=None, column_name=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "cli_id": cli_id,
                           "provider": provider, "model": model,
                           "card_id": card_id, "column_name": column_name})
        if self.fail:
            raise RuntimeError("tmux exploded")
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


# ---- enablement (device-local, KanbanMeta-backed) -------------------------

@pytest.mark.asyncio
async def test_autodispatch_disabled_by_default():
    async with KanbanSessionLocal() as s:
        assert await dispatch.is_autodispatch_enabled(s, PK) is False


@pytest.mark.asyncio
async def test_set_and_list_autodispatch():
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()
        assert await dispatch.is_autodispatch_enabled(s, PK) is True
        assert PK in await dispatch.list_autodispatch_projects(s)
        await dispatch.set_autodispatch(s, PK, False)
        await s.commit()
        assert await dispatch.is_autodispatch_enabled(s, PK) is False
        assert PK not in await dispatch.list_autodispatch_projects(s)


@pytest.mark.asyncio
async def test_disable_all_autodispatch_clears_every_enabled_project():
    other_pk = "git:example.com/me/other-repo"
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await dispatch.set_autodispatch(s, other_pk, True)
        await s.commit()
        assert set(await dispatch.list_autodispatch_projects(s)) == {PK, other_pk}

        await dispatch.disable_all_autodispatch(s)
        await s.commit()

        assert await dispatch.list_autodispatch_projects(s) == []
        assert await dispatch.is_autodispatch_enabled(s, PK) is False
        assert await dispatch.is_autodispatch_enabled(s, other_pk) is False


@pytest.mark.asyncio
async def test_disable_all_autodispatch_is_noop_when_nothing_enabled():
    async with KanbanSessionLocal() as s:
        await dispatch.disable_all_autodispatch(s)
        await s.commit()
        assert await dispatch.list_autodispatch_projects(s) == []


# ---- startup policy: restart disables, hot reload does not -----------------


def _fake_identity(monkeypatch, value):
    """Pin the reloader identity and prove the double actually fired."""
    calls = []

    def _identity():
        calls.append(value)
        return value

    # reset_autodispatch_for_boot resolves this through the module globals, so
    # patching the definition site is also the consumer site here.
    monkeypatch.setattr(dispatch, "_reload_parent_identity", _identity)
    return calls


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_disables_on_a_real_start(monkeypatch):
    calls = _fake_identity(monkeypatch, None)
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()

        assert await dispatch.reset_autodispatch_for_boot(s) is False
        await s.commit()

        assert calls == [None]
        assert await dispatch.list_autodispatch_projects(s) == []
        # No owner marker when we cannot identify a reloader: every later start
        # must keep failing closed.
        assert await s.get(dispatch.KanbanMeta, dispatch.BOOT_OWNER_META_KEY) is None


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_survives_a_hot_reload(monkeypatch):
    calls = _fake_identity(monkeypatch, "2020:11800")
    async with KanbanSessionLocal() as s:
        # First boot under the reloader: still force-off, but record the owner.
        await dispatch.set_autodispatch(s, PK, True)
        assert await dispatch.reset_autodispatch_for_boot(s) is False
        await s.commit()
        assert await dispatch.list_autodispatch_projects(s) == []

        # Operator opts in, then a file change respawns the worker under the
        # *same* reloader -- the flag must stay on.
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()

        assert await dispatch.reset_autodispatch_for_boot(s) is True
        await s.commit()

        assert calls == ["2020:11800"] * 2
        assert await dispatch.is_autodispatch_enabled(s, PK) is True


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_disables_when_the_reloader_is_new(monkeypatch):
    async with KanbanSessionLocal() as s:
        _fake_identity(monkeypatch, "2020:11800")
        await dispatch.reset_autodispatch_for_boot(s)
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()

        # Backend restarted: fresh uvicorn, so a different reloader pid.
        calls = _fake_identity(monkeypatch, "33877:122753")
        assert await dispatch.reset_autodispatch_for_boot(s) is False
        await s.commit()

        assert calls == ["33877:122753"]
        assert await dispatch.list_autodispatch_projects(s) == []
        row = await s.get(dispatch.KanbanMeta, dispatch.BOOT_OWNER_META_KEY)
        assert row is not None and row.value == "33877:122753"


def test_reload_parent_identity_is_none_outside_a_uvicorn_reloader():
    # pytest's parent is not `uvicorn --reload`, so the real probe must decline
    # and let the caller fall back to the force-off default.
    assert dispatch._reload_parent_identity() is None


# ---- startup policy: visibility for the UI --------------------------------
#
# A real backend start force-disables auto-dispatch board-wide. Without an
# explicit UI signal, the symptom reads as "the dispatcher is hanging" — the
# kind of false-attribution that ate 18 minutes on 2026-08-03 (44 cards idle,
# silent for 18 minutes after the merge that triggered the restart). The
# WARNING in logs/ is the only trace. These tests pin the per-project
# visibility marker so the UI can show "force-disabled by backend start at
# <ts>" inline on the toggle component.


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_records_boot_disabled_marker_on_real_start(monkeypatch):
    _fake_identity(monkeypatch, None)
    other_pk = "git:example.com/me/other-repo"
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await dispatch.set_autodispatch(s, other_pk, True)
        await s.commit()

        assert await dispatch.reset_autodispatch_for_boot(s) is False
        await s.commit()

        for pk in (PK, other_pk):
            marker = await dispatch.get_boot_disabled_marker(s, pk)
            assert marker is not None, f"missing marker for {pk}"
            at, reason = marker
            assert isinstance(at, datetime)
            assert reason == "real_backend_start"


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_does_not_record_marker_on_hot_reload(monkeypatch):
    _fake_identity(monkeypatch, "2020:11800")
    async with KanbanSessionLocal() as s:
        # First boot under the reloader is still a real start (no prior marker)
        await dispatch.set_autodispatch(s, PK, True)
        assert await dispatch.reset_autodispatch_for_boot(s) is False
        await s.commit()

        # Operator opts in, hot reload — same reloader. NO marker must land.
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()
        assert await dispatch.reset_autodispatch_for_boot(s) is True
        await s.commit()

        assert await dispatch.get_boot_disabled_marker(s, PK) is None


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_clears_marker_when_operator_opts_in(monkeypatch):
    _fake_identity(monkeypatch, None)
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await dispatch.reset_autodispatch_for_boot(s)
        await s.commit()
        assert await dispatch.get_boot_disabled_marker(s, PK) is not None

        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()

        assert await dispatch.get_boot_disabled_marker(s, PK) is None


@pytest.mark.asyncio
async def test_reset_autodispatch_for_boot_uses_reloader_changed_reason(monkeypatch):
    """When the reloader identity changes between boots, the marker still
    records ``reloader_changed`` (not ``real_backend_start``) — both are
    legitimate policy-driven force-offs, but the UI hint can show different
    copy and the audit trail benefits from the distinction."""
    async with KanbanSessionLocal() as s:
        _fake_identity(monkeypatch, "2020:11800")
        await dispatch.reset_autodispatch_for_boot(s)
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()

        _fake_identity(monkeypatch, "33877:122753")
        await dispatch.reset_autodispatch_for_boot(s)
        await s.commit()

        marker = await dispatch.get_boot_disabled_marker(s, PK)
        assert marker is not None
        assert marker[1] == "reloader_changed"


# ---- prompt ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_prompt_includes_persona_card_and_shipmode():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Build widget")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"description": "Make it blue"})
        await s.flush()
        card = await get_card(s, cid)
    prompt = dispatch.build_card_prompt(
        card, persona="You are the Developer agent.", ship_mode="direct",
    )
    assert "You are the Developer agent." in prompt
    assert "Build widget" in prompt
    assert "Make it blue" in prompt
    assert "Ship mode: direct" in prompt
    assert "cockpit-kanban" in prompt


def test_card_prompt_without_persona_still_works():
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="pull-request")
    assert "Ship mode: pull-request" in prompt
    assert "# T" in prompt


def test_card_prompt_includes_problem_flag_reminder():
    """Every dispatched session should be reminded to file (not just mention)
    problems it notices outside its assigned card's scope — see kanban card
    'Kritische zelf structurering en reflectie' and the flag-problem skill."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
    assert "flag-problem" in prompt
    assert "project-key" in prompt
    assert "create_card" in prompt


def test_card_prompt_executor_phase_has_retro_and_ship_steps():
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                        phase="executor")
    assert "session-retro" in prompt
    assert "merge your branch into master" in prompt
    assert "npm run lint && npm run build" in prompt


def test_card_prompt_warns_against_writes_to_canonical_checkout():
    """Every dispatched session must be told — in the prompt itself, not only
    in the persona doc — that Write/Edit are worktree-relative and that the
    absolute canonical path `/home/vdvgu/claude-cockpit/...` is the *main*
    checkout, where a write silently lands on top of concurrent sessions'
    uncommitted work.

    Background (kanban card 513e37a1a86e41db8b6af8423292f6b6): an analyst
    session edited two docs at `/home/vdvgu/claude-cockpit/docs/cockpit/...`
    instead of its worktree path. Edit succeeded because the committed
    content matched, but the write went to the main checkout and landed on
    top of a concurrent session's uncommitted changes."""
    class _C:
        title = "T"
        description = ""
    # Both phases (executor / analyst) need this — the original incident was
    # an analyst session, and an engineer writing a doc-fix outside its
    # worktree would hit the same blast radius.
    executor_prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct", phase="executor")
    analyst_prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct", phase="analyst")
    for prompt in (executor_prompt, analyst_prompt):
        # Must name both the safe pattern and the forbidden one explicitly —
        # vague "stay in your worktree" guidance is exactly what the original
        # card author already had, and it didn't stick.
        assert "worktree" in prompt.lower()
        assert "/home/vdvgu/claude-cockpit" in prompt
        # Must name the file-mutation tools that can clobber the main checkout.
        assert "Write" in prompt and "Edit" in prompt
        # The callout must precede the ship recipe so it lands in the agent's
        # early context, not buried under later steps.
        assert prompt.index("/home/vdvgu/claude-cockpit") < prompt.index(
            "## Session-end workflow"
        )


def test_card_prompt_callout_interpolates_dispatched_project_path():
    """Regression for kanban card a962b209…: when the dispatcher passes a
    real ``project_path`` + ``worktree_path`` to ``build_card_prompt``, the
    worktree-safety callout must name *those* paths — not the hardcoded
    ``/home/vdvgu/claude-cockpit`` that only holds for the meta-project.
    A dispatched agent that reads "your writable surface is
    ``/home/vdvgu/claude-cockpit/...``" on a non-meta project wrote a
    deliverable into the Cockpit checkout (instead of its own worktree) —
    observable blast-radius was "every product dispatch can leave files in
    Cockpit's tree", in the worst case on top of a concurrent session's
    uncommitted work.
    """
    class _C:
        title = "T"
        description = ""
    project_path = "/scratch/scratchpad/product-claude-cockpit-a462b209"
    worktree_path = (
        f"{project_path}/.claude/worktrees/k-problem-dispa-81b8"
    )
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        project_path=project_path, worktree_path=worktree_path,
    )

    # The callout must name the dispatched project's path as the canonical
    # checkout that is the FORBIDDEN target — not the meta-project path.
    assert project_path in prompt
    # The "sole writable surface" line must point at this session's
    # worktree, with the real branch substituted in for the <branch>
    # placeholder that the legacy hardcode used.
    assert worktree_path in prompt
    # Scope the placeholder check to the callout itself. The rest of the
    # prompt legitimately spells `.claude/worktrees/<branch>/` as *prose*
    # (the ship recipe's browser-count worktree warning, added in
    # a06618bc) — a whole-prompt substring check reads that illustrative
    # path as a fabricated callout claim and fails for the wrong reason.
    callout = dispatch._build_worktree_safety_callout(
        project_path=project_path, worktree_path=worktree_path,
    )
    assert callout in prompt
    assert "<branch>" not in callout

    # The forbidden canonical path inside the callout must match the
    # dispatched project's main checkout, *not* /home/vdvgu/claude-cockpit
    # (which is a totally different tree that the product-project agent
    # has no business writing to). Just check that the callout's Wrong
    # example points at the dispatched project_path, not at the legacy
    # /home/vdvgu/claude-cockpit/ prefix.
    wrong_idx = prompt.find("- **Wrong:**")
    assert wrong_idx != -1, "callout must keep a Wrong example"
    wrong_block = prompt[wrong_idx:wrong_idx + 400]
    assert project_path + "/docs/cockpit" in wrong_block, (
        f"Wrong example should reference the *dispatched* project, got: "
        f"{wrong_block[:200]!r}"
    )


def test_card_prompt_does_not_leak_meta_project_path_for_dispatched_project():
    """AC #3 (kaart a962b209…): a prompt for project X must contain no
    path of project Y. Without the interpolation shipped in this card,
    every dispatched session reads ``/home/vdvgu/claude-cockpit/...`` in
    its worktree-safety callout — even when it was spawned for a
    throwaway product project under ``/scratch/...``.
    """
    class _C:
        title = "T"
        description = ""
    project_path = "/scratch/scratchpad/product-claude-cockpit-a462b209"
    worktree_path = (
        f"{project_path}/.claude/worktrees/k-problem-dispa-81b8"
    )
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        project_path=project_path, worktree_path=worktree_path,
    )

    assert "/home/vdvgu/claude-cockpit" not in prompt, (
        "dispatched project's prompt should not hardcode the meta "
        "project's /home/vdvgu/claude-cockpit path"
    )
    assert (
        "/home/vdvgu/claude-cockpit/.claude/worktrees/<branch>"
        not in prompt
    )
    # And the frontend-gate symlink path must follow the dispatched
    # project, not the meta project's node_modules.
    assert (
        "/home/vdvgu/claude-cockpit/frontend/node_modules"
        not in prompt
    )


def test_card_prompt_callout_falls_back_to_meta_path_when_project_unknown():
    """Backwards-compat (kaart a962b209…): callers that pre-date the
    project_path threading (legacy tests, ad-hoc callers) must still get
    a useful callout with the meta project path as the safe fallback, so
    the existing ``test_card_prompt_warns_against_writes_to_canonical_checkout``
    contract is preserved."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        # no project_path / worktree_path passed
    )
    assert "/home/vdvgu/claude-cockpit" in prompt


def test_ship_instructions_frontend_gate_interpolates_project_path():
    """The frontend-gate symlink-shortcut hardcodes
    ``/home/vdvgu/claude-cockpit/frontend/node_modules`` as the source of
    the symlink — that's the meta project's tree, not the dispatched
    project's. When ``_build_ship_instructions`` gets a real project_path,
    it must interpolate the project's own ``frontend/node_modules`` so a
    product-project agent doesn't symlink Cockpit's deps into its own
    worktree."""
    project_path = "/scratch/scratchpad/product-claude-cockpit-a462b209"
    instructions = dispatch._build_ship_instructions(
        "direct", project_path=project_path,
    )
    assert f"{project_path}/frontend/node_modules" in instructions
    assert (
        "/home/vdvgu/claude-cockpit/frontend/node_modules"
        not in instructions
    )


def test_ship_instructions_frontend_gate_quotes_path_with_spaces():
    """FCR blocker 2 (kaart a962b209…): when ``project_path`` itself
    contains whitespace (e.g. a project named ``My Project``) or shell
    metacharacters (``prod$1``), the interpolated path inside the bash
    snippet must be shell-quoted — otherwise ``[ -d … ]`` splits on
    the space and ``ln -s …`` creates a symlink to a partial path.
    The unquoted legacy ``/home/vdvgu/claude-cockpit/frontend/node_modules``
    happened to contain no space, so the bug was silent for the meta
    project; a product project triggers it on the first dispatch.

    The dispatcher uses ``shlex.quote``, which wraps the path in single
    quotes (FCR blocker C kaart a962b209…). Single-quoted shell strings
    are stricter than double-quoted ones — a path containing ``$``,
    backticks, or embedded ``"`` stays literal because ``sh`` performs
    no expansion or quote interpretation inside ``'…'``.
    """
    import shlex as _shlex

    project_with_spaces = "/home/me/My Project/claude-cockpit"
    instructions = dispatch._build_ship_instructions(
        "direct", project_path=project_with_spaces,
    )
    # The `[ -d … ]` test and the `ln -s …` source must both wrap the
    # path in shell quotes. Compute the expected quoted form via the
    # same ``shlex.quote`` the dispatcher uses so the assertion stays
    # in lockstep with the implementation.
    expected_bin = _shlex.quote(
        f"{project_with_spaces}/frontend/node_modules/.bin"
    )
    expected_nm = _shlex.quote(
        f"{project_with_spaces}/frontend/node_modules"
    )
    assert (
        f"[ -d {expected_bin} ]" in instructions
    ), (
        f"frontend-gate `test -d` must shell-quote project_path; "
        f"expected `[ -d {expected_bin} ]` in instructions"
    )
    assert (
        f"ln -s {expected_nm} node_modules" in instructions
    ), (
        f"frontend-gate `ln -s` source must shell-quote project_path; "
        f"expected `ln -s {expected_nm} node_modules` in instructions"
    )


def test_ship_instructions_frontend_gate_quotes_path_with_metacharacter():
    """FCR blocker C (kaart a962b209…): the spaces test only exercises
    whitespace; a path with a shell metacharacter (``$1``, backticks,
    embedded ``"``, backslashes) breaks *double-quote* wrapping because
    ``sh`` still expands ``$`` and backticks and tokenises on embedded ``"``.
    ``shlex.quote`` uses single quotes for that reason — a literal
    round-trip through ``shlex.split`` proves the path is shell-safe.
    """
    import shlex as _shlex

    project_with_meta = "/tmp/prod$1/claude-cockpit"
    instructions = dispatch._build_ship_instructions(
        "direct", project_path=project_with_meta,
    )
    # Pick out the two bash lines we care about and round-trip them
    # through ``shlex.split``; the third positional arg of ``ln -s``
    # must be the literal path, not a shell-expanded variant.
    ln_line = next(
        line for line in instructions.splitlines() if "ln -s" in line
    )
    parts = _shlex.split(ln_line.strip().rstrip("\\"))
    assert len(parts) >= 3, f"unexpected `ln -s` line: {ln_line!r}"
    assert parts[0] == "ln"
    assert parts[1] == "-s"
    assert parts[2] == f"{project_with_meta}/frontend/node_modules", (
        f"shlex.split round-trip lost the literal $1: got {parts[2]!r}"
    )
    # Same round-trip for the `[ -d … ]` test source. The template
    # renders two `[ -d` lines: one checks the inner worktree's
    # ``node_modules``, the other probes the main checkout's ``.bin``.
    # Pick the one whose shlex-split args include the project path.
    test_lines = [
        line for line in instructions.splitlines()
        if "[ -d" in line and "node_modules/.bin" in line
    ]
    matched = None
    for candidate in test_lines:
        try:
            test_parts = _shlex.split(candidate.strip().rstrip("\\"))
        except ValueError:
            continue
        if f"{project_with_meta}/frontend/node_modules/.bin" in test_parts:
            matched = test_parts
            break
    assert matched is not None, (
        f"could not find a `[ -d … ]` line whose shlex.split yields "
        f"the literal project path; candidates: {test_lines!r}"
    )


def test_card_prompt_callout_omits_worktree_path_for_resume_session():
    """FCR blocker 1 (kaart a962b209…): when the card carries a
    ``resume_session_id``, ``_run_card`` must NOT fabricate a worktree
    path keyed on the brand-new mint — the prior session's cwd comes
    from ``resume_project_folder`` and may live under a completely
    different branch name. Passing the freshly-minted path would lie
    to the agent about where its shell actually starts.

    AC2 (kaart a962b209…): when ``worktree_path`` is not passed (resume,
    sandcastle, or headless transports), the callout must render
    *neutral* cwd guidance instead of claiming the agent was spawned
    in a git worktree at a fabricated path. The legacy ``<branch>``
    placeholder fallback was a hidden lie — the resume session's cwd
    has nothing to do with a worktree branch name minted seconds ago.
    """
    class _C:
        title = "T"
        description = ""

    # Mirror the dispatcher-side guard by calling build_card_prompt
    # directly: the FCR's specific failure mode is that an unconditional
    # ``worktree_path`` interpolates into the prompt. We exercise that
    # by passing a project_path without a worktree_path and asserting
    # the resulting prompt does NOT claim a fresh-worktree spawn.
    prompt = dispatch.build_card_prompt(
        _C(), persona=None, ship_mode="direct",
        project_path="/scratch/somewhere",
        # worktree_path omitted — simulates a resume / sandcastle /
        # headless dispatch where the cwd is determined by the
        # transport, not a freshly-minted worktree.
    )
    # AC2 fix: the legacy "You were spawned in a git worktree at
    # ``<project>/.claude/worktrees/<branch>``" framing must NOT
    # appear when worktree_path is unknown — that's a fabricated
    # claim about a directory that doesn't exist for resume/sandcastle
    # /headless transports.
    # Scoped to the callout for the same reason as the sibling test above:
    # the ship recipe's prose spells the placeholder path legitimately.
    callout = dispatch._build_worktree_safety_callout(
        project_path="/scratch/somewhere", worktree_path=None,
    )
    assert callout in prompt
    assert "<branch>" not in callout, (
        "When worktree_path is not passed, the callout must NOT "
        "fabricate a `<project>/.claude/worktrees/<branch>` path — "
        "that's a lie to the agent about its real cwd."
    )
    # The neutral framing mentions the resume/sandcastle/headless
    # carve-out so the agent knows why no worktree path is given.
    assert "resume / sandcastle / headless" in prompt or (
        "freshly-minted git worktree" in prompt
    ), (
        "When worktree_path is not passed, the callout must explain "
        "why (resume / sandcastle / headless skip the worktree step)."
    )
    # And the actual project_path is still honored as the canonical
    # forbidden target.
    assert "/scratch/somewhere" in prompt


def test_worktree_transport_detected_regardless_of_factory_instance():
    """AC2 runtime regression (kaart a962b209…): the ``_run_card`` predicate
    that decides whether to interpolate a real worktree path must recognise
    the worktree transport *regardless of which factory instance produced
    it*.

    The bug: the old predicate was ``card_transport is worktree_transport``
    — an object-identity check against the module-level singleton. But on
    the normal route (``card.transport is None``), the dispatcher resolves
    the transport via ``get_transport_for_project`` →
    ``make_worktree_transport(skip_permissions=skip)``, which returns a
    *fresh* closure that is NOT identical to the singleton. So identity
    returned False, ``worktree_path`` became None, and every ordinary
    dispatched agent — which really IS in a fresh worktree — read the
    neutral resume/sandcastle/headless framing instead of its real
    write-address. The 7 earlier tests only called ``build_card_prompt``
    with explicit args, so they never touched this predicate.
    """
    # The module singleton (only used when card.transport == "worktree")
    assert dispatch._transport_is_worktree(dispatch.worktree_transport)
    # A FRESH closure — exactly what the normal route hands to _run_card.
    # This is the case the old ``is`` check missed.
    fresh = dispatch.make_worktree_transport(skip_permissions=False)
    assert fresh is not dispatch.worktree_transport
    assert dispatch._transport_is_worktree(fresh)
    # Non-worktree transports must NOT be misclassified as fresh worktrees.
    assert not dispatch._transport_is_worktree(dispatch.sandcastle_transport)
    assert not dispatch._transport_is_worktree(
        dispatch.make_resume_transport("sid-123", "some-folder")
    )


def test_normal_route_transport_resolution_yields_worktree_path():
    """AC2 (kaart a962b209…) via the transport-resolution path, not direct
    ``build_card_prompt`` args: a card with ``transport=None`` resolved the
    way ``_run_card`` resolves it (``get_transport_for_card(card, default)``
    where ``default`` is a fresh worktree closure from
    ``get_transport_for_project``) must be recognised as a fresh-worktree
    transport, so the callout names the real worktree path.
    """
    card = SimpleNamespace(transport=None, resume_session_id=None,
                           resume_project_folder=None)
    # What get_transport_for_project returns on the normal route: a fresh
    # closure, NOT the module singleton.
    default = dispatch.make_worktree_transport(skip_permissions=False)
    card_transport = dispatch.get_transport_for_card(card, default)
    is_fresh_worktree = dispatch._transport_is_worktree(card_transport)
    assert is_fresh_worktree, (
        "normal-route (transport=None) card must resolve to a worktree "
        "transport recognised by the callout predicate"
    )

    # And the prompt built from the path this predicate unlocks names the
    # real worktree, not the neutral resume/sandcastle/headless framing.
    project_path = "/scratch/scratchpad/product-x"
    name = "k-normal-route-1234"
    worktree_path = (
        str(Path(project_path) / ".claude" / "worktrees" / name)
        if is_fresh_worktree else None
    )
    prompt = dispatch.build_card_prompt(
        SimpleNamespace(title="T", description=""),
        persona=None, ship_mode="direct",
        project_path=project_path, worktree_path=worktree_path,
    )
    assert worktree_path in prompt
    assert "You were spawned in a git worktree at" in prompt
    assert "it is **not** a freshly-minted git worktree" not in prompt


def test_direct_ship_recipe_has_uncommitted_changes_preflight():
    """Direct-mode ship recipe must guard against the silent no-op where the
    detached worktree only sees COMMITTED state: uncommitted/untracked changes
    in the source worktree merge as "Everything up-to-date" instead of shipping.
    A pre-flight check must abort with an explicit error before the merge."""
    instructions = dispatch._build_ship_instructions("direct")
    # Pin the `--` separator: a tracked file named HEAD makes the unguarded
    # form exit 128 (`ambiguous argument 'HEAD'`) and turn every ship into
    # a bogus "uncommitted changes" abort (kanban card 7dd8a3dd…).
    assert "git diff --quiet HEAD --" in instructions
    assert "ls-files --others --exclude-standard" in instructions
    assert "uncommitted" in instructions
    # The guard must sit before the detached-worktree merge it protects.
    assert instructions.index("git diff --quiet HEAD --") < instructions.index(
        "git worktree add --detach"
    )


def test_card_prompt_analyst_phase_has_retro_but_no_ship_steps():
    """Analyst cards are planning-only: they get the retro step and the
    move-to-Done exit, but none of the engineer merge/frontend-ship steps
    (see docs/cockpit/headless-session-retro-decision.md)."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                        phase="analyst")
    assert "session-retro" in prompt
    assert "Move the parent card to Done" in prompt
    assert "merge your branch into master" not in prompt
    assert "npm run lint && npm run build" not in prompt


# ---- dispatch_project: the core ------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_claims_moves_to_doing_and_spawns():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/home/me/repo", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.column == "engineer"
    assert card.claimed_by.startswith("agent:")
    assert len(transport.calls) == 1
    # claimant label == the spawned session name
    assert transport.calls[0]["session_name"] == card.claimed_by.split("agent:", 1)[1]
    assert transport.calls[0]["directory"] == "/home/me/repo"


@pytest.mark.asyncio
async def test_dispatch_defaults_to_claude_code_provider():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["cli_id"] == "claude-code"


@pytest.mark.asyncio
async def test_dispatch_threads_card_provider_to_transport():
    """A provider id chosen in the UI selects the spawned CLI, but must not be
    mistaken for a persona/column (there is no `mimo-code` agent column)."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "mimo-code"},
        )
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="mimo-code",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert len(transport.calls) == 1
    assert transport.calls[0]["cli_id"] == "mimo-code"
    assert card.column == "engineer"  # provider id is NOT used as the column


@pytest.mark.asyncio
async def test_dispatch_defaults_to_anthropic_platform():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_provider():
    """A column configured with default_provider="minimax" (e.g. an "engineer"
    column meant for bulk coding work) routes its cards' spawn to that platform,
    while columns without one keep the default Anthropic subscription."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "engineer"
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


# ---- model precedence: card.model > column.default_model > persona frontmatter ----

@pytest.mark.asyncio
async def test_dispatch_no_model_by_default():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] is None


@pytest.mark.asyncio
async def test_dispatch_uses_card_model_over_everything():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"model": "opus"})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_model_when_card_model_unset():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_persona_frontmatter_model(tmp_path):
    transport = RecordingTransport()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'claude-opus-4-8'\n---\nBe an engineer.\n"
    )
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "claude-opus-4-8"


@pytest.mark.asyncio
async def test_dispatch_card_model_beats_persona_frontmatter(tmp_path):
    # Regression guard for kanban-chore k-chore-sonnet-*. The engineer
    # persona now defaults to `sonnet` in frontmatter; if a card needs the
    # heavier Opus (or any other model) it sets card.model. The override
    # MUST beat the persona frontmatter -- otherwise the per-card escalation
    # route is hollow and the Sonnet-default change is not safely reversible
    # per card. See docs/cockpit/kanban-model-override.md.
    transport = RecordingTransport()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'sonnet'\n---\nBe an engineer.\n"
    )
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"model": "opus"})
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_column_default_model_beats_persona_frontmatter(tmp_path):
    # Same property, one level up: column.default_model must beat the persona
    # frontmatter. Lets an operator pin "all engineer work to opus again"
    # without having to flip card.model on every backlog entry.
    transport = RecordingTransport()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'sonnet'\n---\nBe an engineer.\n"
    )
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="opus",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "opus"


def test_effective_model_precedence():
    # per-column override > card.model > column.default_model > persona frontmatter
    assert dispatch._effective_model("m5", "opus", "sonnet", "haiku") == "m5"
    assert dispatch._effective_model(None, "opus", "sonnet", "haiku") == "opus"
    assert dispatch._effective_model(None, None, "sonnet", "haiku") == "sonnet"
    assert dispatch._effective_model(None, None, None, "haiku") == "haiku"
    assert dispatch._effective_model(None, None, None, None) is None
    assert dispatch._effective_model("", "", "", "") is None


def test_effective_model_persona_fallback_suppressed_for_non_anthropic():
    # A persona `model:` alias (e.g. "opus") is Anthropic-only. When the column
    # routes to a non-Anthropic provider it must NOT leak in as --model, so the
    # provider env's native model (e.g. MiniMax-M3) stays in effect.
    assert dispatch._effective_model(None, None, None, "opus", provider="minimax") is None
    assert dispatch._effective_model(None, None, None, "opus", provider="bedrock") is None
    # Anthropic (or unknown/None provider) keeps the persona fallback.
    assert dispatch._effective_model(None, None, None, "opus", provider="anthropic") == "opus"
    assert dispatch._effective_model(None, None, None, "opus", provider=None) == "opus"
    # Explicit card / override models still win for any provider — they are a
    # deliberate authoring choice that may name a provider-native model. The
    # column-default alias, however, is now provider-gated (see the test below);
    # here no higher layer pinned the provider, so it is kept.
    assert dispatch._effective_model(None, None, "MiniMax-M3", "opus", provider="minimax") == "MiniMax-M3"
    assert dispatch._effective_model(None, "MiniMax-M3", None, "opus", provider="minimax") == "MiniMax-M3"
    assert dispatch._effective_model("MiniMax-M3", None, None, "opus", provider="minimax") == "MiniMax-M3"


def test_effective_model_column_default_dropped_when_higher_layer_switches_provider():
    # A column-default model alias (e.g. "opus") is native to the column's own
    # provider. When a HIGHER layer (global_override / pool-spillover) pins the
    # spawn to a DIFFERENT provider than column.default_provider, that alias is
    # meaningless there and must fall through to the provider-native default.
    assert dispatch._effective_model(
        None, None, "opus", None, provider="minimax",
        column_default_provider="anthropic", provider_pinned_by_higher_layer=True,
    ) is None
    # Column with no default_provider is implicitly Anthropic; a higher-layer
    # minimax pin still differs from it -> alias dropped.
    assert dispatch._effective_model(
        None, None, "opus", None, provider="minimax",
        column_default_provider=None, provider_pinned_by_higher_layer=True,
    ) is None
    # Higher layer pins the SAME provider as the column default -> model kept
    # (provider and model came from a consistent layer).
    assert dispatch._effective_model(
        None, None, "MiniMax-M3", None, provider="minimax",
        column_default_provider="minimax", provider_pinned_by_higher_layer=True,
    ) == "MiniMax-M3"
    # No higher-layer pin (provider from a per-card column_override or the column
    # default itself) -> model kept. This preserves the provider-only per-card
    # override fallthrough contract (AC1 scopes the drop to global_override/pool).
    assert dispatch._effective_model(
        None, None, "opus", None, provider="bedrock",
        column_default_provider=None, provider_pinned_by_higher_layer=False,
    ) == "opus"
    # Explicit card.model / per-column override model always win, even on a
    # mismatched higher-layer provider (AC4).
    assert dispatch._effective_model(
        "sonnet-5", None, "opus", None, provider="minimax",
        column_default_provider="anthropic", provider_pinned_by_higher_layer=True,
    ) == "sonnet-5"
    assert dispatch._effective_model(
        None, "sonnet-5", "opus", None, provider="minimax",
        column_default_provider="anthropic", provider_pinned_by_higher_layer=True,
    ) == "sonnet-5"


# ---- per-card column_overrides: model+provider per target column ----------

@pytest.mark.asyncio
async def test_dispatch_column_override_beats_column_defaults():
    """Parent-card scenario: an engineer column defaulting to minimax/M3, but a
    card carrying a per-column override for "engineer" spawns with the override's
    provider AND model instead — even though Sonnet 5 lives only on Anthropic."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3[1m]",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"model": "sonnet-5", "provider": "anthropic"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "sonnet-5"


@pytest.mark.asyncio
async def test_dispatch_column_override_beats_card_model():
    """A per-column override outranks card.model (the card-global override)."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"model": "opus", "column_overrides": {
                "engineer": {"model": "sonnet-5", "provider": "anthropic"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "sonnet-5"


@pytest.mark.asyncio
async def test_dispatch_column_override_provider_only_leaves_model_fallthrough():
    """An override may set provider without model: the provider is overridden but
    the model still falls through to column.default_model / persona / None."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_column_override_for_other_column_is_ignored():
    """An override keyed on a column other than the dispatch target has no effect
    — behaves as if no override existed for the resolved column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "analyst": {"model": "sonnet-5", "provider": "bedrock"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    # card dispatches into "engineer"; only an "analyst" override exists -> no effect
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] is None


@pytest.mark.asyncio
async def test_dispatch_without_column_overrides_is_backwards_compatible():
    """A card with column_overrides=None dispatches exactly as it does today."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)
        assert card.column_overrides is None
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1


# ---- subagent_caps env injection (kanban card aaa81b23…) ------------------
#
# The dispatch path translates ``card.column_overrides[target].subagent_caps``
# into CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH / CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS
# (and the two session-cap siblings) so per-lane caps take effect at CC spawn
# time. The helper is pure and the wire-up runs in the worktree transport's
# extra_env — captured via ``dispatch._subagent_caps_to_env`` directly here
# (it is the unit-level contract) plus a RecordingTransport-level smoke
# (the dispatch path itself doesn't crash when the override is set).

_SUBAGENT_CAPS_TO_ENV_DEPTH_AND_CONCURRENT = {
    "max_spawn_depth": 3,
    "max_concurrent": 20,
}


@pytest.mark.asyncio
async def test_subagent_caps_to_env_maps_known_keys():
    """The helper emits the documented env vars for the two load-bearing keys
    (max_spawn_depth -> MAX_SUBAGENT_SPAWN_DEPTH; max_concurrent ->
    MAX_CONCURRENT_SUBAGENTS). Without this wire the override would round-trip
    through the API but never reach the spawned CLI."""
    from app.kanban.dispatch import _subagent_caps_to_env
    env = _subagent_caps_to_env(_SUBAGENT_CAPS_TO_ENV_DEPTH_AND_CONCURRENT)
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "3"
    assert env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "20"


@pytest.mark.asyncio
async def test_subagent_caps_to_env_maps_session_cap_keys():
    """The session-cap siblings (per-session subagent + web search) are emitted
    when the override sets them — Claude Code 2.1.212 introduced these and they
    are part of the same caps family."""
    from app.kanban.dispatch import _subagent_caps_to_env
    env = _subagent_caps_to_env({
        "max_subagents_per_session": 50,
        "max_web_searches_per_session": 10,
    })
    assert env["CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION"] == "50"
    assert env["CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION"] == "10"


@pytest.mark.asyncio
async def test_subagent_caps_to_env_returns_empty_for_none_or_empty():
    """None / empty input -> empty dict, so the worktree transport's
    ``{**extra_env, **subagent_caps_env}`` merge is a no-op when the card
    doesn't carry the override."""
    from app.kanban.dispatch import _subagent_caps_to_env
    assert _subagent_caps_to_env(None) == {}
    assert _subagent_caps_to_env({}) == {}


@pytest.mark.asyncio
async def test_subagent_caps_to_env_ignores_unknown_keys():
    """The schema already rejects unknown keys at the API boundary; the
    helper is the last line of defence and silently drops anything that
    somehow slips through (e.g. a stale card row from before the schema
    validator existed). A crash here would block every spawn on that card."""
    from app.kanban.dispatch import _subagent_caps_to_env
    env = _subagent_caps_to_env({"max_banana": 5, "max_spawn_depth": 2})
    assert "CLAUDE_CODE_MAX_BANANA" not in env
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "2"


@pytest.mark.asyncio
async def test_dispatch_with_subagent_caps_does_not_crash():
    """Smoke: a card carrying column_overrides[col].subagent_caps dispatches
    end-to-end without raising. The env wire-up lives in the worktree
    transport closure, which is NOT this test's transport (RecordingTransport
    bypasses it); the helper above covers the actual env generation, and this
    test guards against the card-level path crashing before the closure runs."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {
                    "subagent_caps": {"max_spawn_depth": 3, "max_concurrent": 20},
                },
            }})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] is None


# The two tests below share one card-shaped override dict spanning both the
# "analyst" and "engineer" columns and prove each phase resolves its OWN entry,
# because the lookup is keyed on the phase's resolved target column.
_BOTH_PHASE_OVERRIDES = {
    "analyst": {"model": "opus", "provider": "anthropic"},
    "engineer": {"model": "MiniMax-M3[1m]", "provider": "minimax"},
}


@pytest.mark.asyncio
async def test_dispatch_analyst_target_uses_analyst_override(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("You are the Analyst.")
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "analyst",
                                    "column_overrides": _BOTH_PHASE_OVERRIDES})
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_engineer_target_uses_engineer_override():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": _BOTH_PHASE_OVERRIDES})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"
    assert transport.calls[0]["model"] == "MiniMax-M3[1m]"


@pytest.mark.asyncio
async def test_persona_override_still_routes_to_persona_column():
    """A non-provider agent_override (a persona name) keeps acting as the column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="developer",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "developer"
    assert transport.calls[0]["cli_id"] == "claude-code"


@pytest.mark.asyncio
async def test_dispatch_picks_first_todo_by_rank():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        first = await _make_card(s, title="A")
        await _make_card(s, title="B")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, first)
    assert card.column == "engineer"        # first card got picked
    assert len(transport.calls) == 2        # both dispatchable cards get claimed


@pytest.mark.asyncio
async def test_dispatch_card_bypasses_busy_cap():
    """Manual dispatch_card runs a card even while the project is busy."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        target = await _make_card(s, title="manual", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=target, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, target)
    assert result is not None
    assert card.column == "engineer"
    assert len(transport.calls) == 1


# ---- per-project session cap ----------------------------------------------

def _bare_card(column, claimed_by):
    c = KanbanCard(id="x", project_key=PK, title="t", description="",
                   column=column, rank="1")
    c.claimed_by = claimed_by
    return c


def test_active_session_count_counts_agent_claims_in_agent_columns():
    cards = [
        _bare_card("engineer", "agent:a"),
        _bare_card("review", "agent:b"),
        _bare_card("Backlog", "agent:c"),   # fixed column: excluded
        _bare_card("Done", "agent:d"),       # fixed column: excluded
        _bare_card("engineer", "me@ui"),     # human claim: excluded
        _bare_card("engineer", None),         # unclaimed: excluded
    ]
    assert dispatch._active_session_count(cards) == 2


@pytest.mark.asyncio
async def test_dispatch_fills_every_pending_card_in_one_tick():
    """Without a project-level cap, dispatch_project dispatches every
    dispatchable card in a single tick; per-column caps (when set) are the only
    structural limit at the dispatcher level."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        for i in range(4):
            await _make_card(s, title=f"c{i}", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 4


# ---- per-column session cap -------------------------------------------------


def test_active_session_count_by_column():
    cards = [
        _bare_card("engineer", "agent:a"),
        _bare_card("review", "agent:b"),
        _bare_card("Backlog", "agent:c"),
        _bare_card("Done", "agent:d"),
        _bare_card("engineer", "me@ui"),
        _bare_card("engineer", None),
        _bare_card("analyst", "agent:e"),
    ]
    counts = dispatch._active_session_count_by_column(cards)
    assert counts == {"engineer": 1, "review": 1, "analyst": 1}


@pytest.fixture
def project_with_agents(tmp_path):
    """Create a temporary project with agent persona files so column resolution
    resolves agent names (engineer, review) to their agent columns instead of
    falling through to the hardcoded 'engineer' default."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("engineer", "review"):
        (agents_dir / f"{name}.md").write_text(f"# {name}")
    return str(tmp_path)


@pytest.mark.asyncio
async def test_dispatch_respects_per_column_cap(project_with_agents):
    """When a column has per-column max_sessions, the dispatcher stops
    dispatching cards to that column once the cap is reached."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="review",
                                     default_agent="review", max_sessions=2)
        for i in range(4):
            cid = await _make_card(s, title=f"eng-{i}", column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "engineer"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_per_column_cap_does_not_block_other_columns(project_with_agents):
    """Per-column caps apply independently: a full engineer column doesn't
    block cards targeting the review column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="review",
                                     default_agent="review", max_sessions=2)
        # Fill the engineer slot first
        busy_id = await _make_card(s, title="eng-busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy_id, payload={"claimed_by": "agent:k-eng-0001"},
        )
        cid = await _make_card(s, title="eng-2", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        for title in ("rev-1", "rev-2"):
            cid = await _make_card(s, title=title, column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "review"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_column_cap_defaults_null_means_no_per_column_limit(project_with_agents):
    """A column with max_sessions=NULL (unset) does not gate dispatch -- all
    dispatchable cards in that column get claimed in one tick."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=None)
        await service.create_column(s, project_key=PK, name="analyst",
                                     default_agent="analyst", max_sessions=None)
        for i in range(4):
            cid = await _make_card(s, title=f"task-{i}", column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "engineer"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 4


@pytest.mark.asyncio
async def test_zero_column_cap_blocks_dispatch(project_with_agents):
    """max_sessions=0 pauses dispatch to that target column without claiming it."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=0)
        cid = await _make_card(s, title="paused", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)

    assert result is None
    assert transport.calls == []
    assert card.column == "Backlog"
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_zero_column_cap_does_not_block_other_columns(project_with_agents):
    """A paused target column is skipped so another target can dispatch in the same tick."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=0)
        await service.create_column(s, project_key=PK, name="review",
                                     default_agent="review", max_sessions=None)
        paused_id = await _make_card(s, title="paused", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=paused_id, payload={"agent": "engineer"},
        )
        review_id = await _make_card(s, title="reviewable", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=review_id, payload={"agent": "review"},
        )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        paused = await get_card(s, paused_id)
        review = await get_card(s, review_id)

    assert result is not None
    assert len(transport.calls) == 1
    assert paused.column == "Backlog"
    assert paused.claimed_by is None
    assert review.column == "review"
    assert (review.claimed_by or "").startswith("agent:")


# ---- manual per-subscription pause (kaart f056b2888a…) --------------------
#
# Column pause is per-column; manual subscription pause is per-provider.
# When the operator toggles a provider off, every card whose resolved provider
# matches must stay in Backlog for that tick -- regardless of which column it
# would have targeted. A card on a different provider must still dispatch.


@pytest.mark.asyncio
async def test_manual_subscription_pause_blocks_dispatch(project_with_agents):
    """A manually-paused provider holds its cards back: the dispatcher skips
    them without claiming or moving them, while cards on other providers
    dispatch normally in the same tick."""
    from app.kanban import dispatch_pause

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Two columns, each pinned to a different provider via default_provider.
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        await service.create_column(
            s, project_key=PK, name="review", default_agent="review",
            default_provider="bedrock",
        )
        paused_id = await _make_card(s, title="paused-anthropic", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=paused_id, payload={"agent": "engineer"},
        )
        review_id = await _make_card(s, title="bedrock-card", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=review_id, payload={"agent": "review"},
        )
        # Pause anthropic manually.
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        paused = await get_card(s, paused_id)
        review = await get_card(s, review_id)

    assert result is not None
    # Only the bedrock card was dispatched; the anthropic card stayed put.
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert paused.column == "Backlog"
    assert paused.claimed_by is None
    assert review.column == "review"
    assert (review.claimed_by or "").startswith("agent:")


@pytest.mark.asyncio
async def test_manual_subscription_pause_respects_column_override_provider(project_with_agents):
    """The gate uses the resolved provider (per-card column override > column
    default > PROVIDER_ANTHROPIC), not just the column default. A card whose
    column is 'engineer' (default anthropic) but whose column_overrides pin
    provider=bedrock must dispatch when anthropic is paused."""
    from app.kanban import dispatch_pause

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        # Card with a per-card override routing it to bedrock despite the
        # column's anthropic default.
        cid = await _make_card(s, title="overridden", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={
                "agent": "engineer",
                "column_overrides": {"engineer": {"provider": "bedrock"}},
            },
        )
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        dispatched = await get_card(s, cid)

    assert result is not None
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert dispatched.column == "engineer"


@pytest.mark.asyncio
async def test_unpausing_manual_subscription_resumes_dispatch(project_with_agents):
    """After the operator toggles the manual pause off, dispatch resumes for
    that provider's cards in the next tick."""
    from app.kanban import dispatch_pause

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        cid = await _make_card(s, title="anthropic-card", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        # Pause, then immediately unpause -- the row should be cleared.
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await dispatch_pause.set_manual_pause(s, "anthropic", False)
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        dispatched = await get_card(s, cid)

    assert result is not None
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert dispatched.column == "engineer"


# ---- manual subscription pause: precedence chain (FCR follow-up) -----------
#
# The operator's mental model is "pause everything on subscription X" — and
# the dispatcher should honour that against the provider the card will
# *actually* spawn against. The card's actual provider is determined by
# the full chain (global override → pool choice → per-card column
# override → column default → PROVIDER_ANTHROPIC). These tests pin the
# gate to that chain; the previous helper only walked the last two
# layers and broke the subscription-level semantics (FCR kaart
# f056b2888a…).

@pytest.mark.asyncio
async def test_manual_pause_respects_global_subscription_override(project_with_agents):
    """A board-wide subscription override pins the spawn to provider X.
    Pausing X (and not the column default) must hold the card back, even
    though the column default is a different provider.

    Pre-fix the gate used ``_provider_for_card`` which only consulted the
    column override/default, so a board pinned to MiniMax with an
    anthropic column default would have wrongly held the card on an
    anthropic pause (and missed the real minimax pause)."""
    from app.kanban import dispatch_pause
    from app.kanban.dispatch import set_active_subscription_override

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        cid = await _make_card(s, title="override-routed", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        # Board pins every spawn to MiniMax; column default would route to
        # anthropic without the override.
        await set_active_subscription_override(
            s, PK, {"provider": "minimax", "model": None},
        )
        # Pause minimax — the gate must respect the override.
        await dispatch_pause.set_manual_pause(s, "minimax", True)
        await s.commit()

        await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        held_back = await get_card(s, cid)
    # No card was dispatched (gate held the only one back).
    # The card stayed put — the gate caught the override-routed spawn.
    assert transport.calls == []
    assert held_back.column == "Backlog"
    assert held_back.claimed_by is None


@pytest.mark.asyncio
async def test_manual_pause_respects_subscription_pool_choice(project_with_agents, monkeypatch):
    """Kaart 0172e94d…: de dispatch-gate moet de resolved provider
    volgen, ongeacht of die van de kolom-default (kop) of van de
    pool-staart komt. Een pauze op de effectieve provider houdt de
    kaart tegen — ook wanneer de resolver via de spillover-keten tot
    die provider komt.

    Vóór deze kaart testte dit "pool's eigen provider vs. kolom-default
    zijn verschillend". Nu zijn ze per definitie aan elkaar gelijk
    (de kop ís de kolom-default, de staart is de rest) — de test
    is daarom herschreven rond "kop = pool-entry met drempel; pauzeer
    die provider → gate houdt tegen". De gate gebruikt
    ``resolve_effective_provider_and_model`` (kaart 8da646d8…) en
    ziet dus dezelfde chain als de spawn-call.
    """
    # Pool snapshots are no-ops in this test environment; the pool router
    # falls through to "first entry of cli_id wins" when no signal is
    # available, which is enough to drive the precedence chain.
    from app.kanban import dispatch_pause
    from app.kanban.subscription_pool import PoolEntry, set_subscription_pool
    async def _no_snapshots(entries):
        return {}
    # monkeypatch, never a raw module assignment: a bare
    # ``dispatch._gather_pool_usage_snapshots = _no_snapshots`` is never
    # undone, so the stub leaked into every later test module in a
    # full-suite run. ``test_subscription_pool_dispatch.py`` sorts after
    # this file and calls the real gatherer — it got ``{}`` back and three
    # of its tests failed on CI while passing in isolation.
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="bedrock",
        )
        cid = await _make_card(s, title="pool-routed", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        # Pool contains ONLY bedrock, dezelfde provider als de kolom-
        # default. De kop erft pool-entry-drempel; met geen signal is
        # de kop beschikbaar en wint. De card zou dus tegen bedrock
        # dispatchen.
        await set_subscription_pool(s, PK, [
            PoolEntry(provider="bedrock", model=None, drempel=0.9),
        ])
        # Pause bedrock — de gate moet de resolved provider volgen en
        # de kaart vasthouden.
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        await s.commit()

        await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        held_back = await get_card(s, cid)

    # Card held back because the effective provider (bedrock, via de
    # spillover-keten) is paused.
    assert transport.calls == [], (
        f"expected no spawns (gate should have held card back), "
        f"got: {[(c.get('session_name'), c.get('provider')) for c in transport.calls]}"
    )
    assert held_back.column == "Backlog"
    assert held_back.claimed_by is None

    # Now unpause bedrock. Pool + kolom-default geven bedrock, gate
    # laat de spawn door. (De stub uit het eerste deel staat er nog —
    # monkeypatch draait pas terug bij teardown.)
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, "bedrock", False)
        await s.commit()

        await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        dispatched = await get_card(s, cid)

    # Kop (bedrock) wint — card dispatcht op bedrock.
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert dispatched.column == "engineer"


@pytest.mark.asyncio
async def test_pick_pool_choice_excludes_manually_paused_providers(project_with_agents, monkeypatch):
    """De pool-router (``_pick_pool_choice``) moet de handmatige
    pause-lijst van de operator samenvoegen met de tijdgebonden
    lijst voordat de pool wordt geraadpleegd, zodat de picker weet
    dat de operator's subscription off-limits is. De picker mag
    zelfs een gepauzeerde entry als "laatste-val-terug"-fallback
    teruggeven (zie ``pick_subscription_for_cli``); de dispatch-gate
    is wat de spawn blokkeert. End-to-end: een pool wiens enige
    entry handmatig is gepauzeerd moet GEEN card spawnen, zelfs
    niet wanneer de picker die entry teruggeeft.

    Kaart 0172e94d…: kolom-default = pool's enige entry; de kop
    komt dus over de pool heen en de resolver levert dezelfde
    provider ongeacht welke tak van de keten wint. De gate volgt
    die resolved provider."""
    from app.kanban import dispatch_pause
    from app.kanban.subscription_pool import PoolEntry, set_subscription_pool
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="bedrock",
        )
        cid = await _make_card(s, title="pool-paused", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        # Pool: enige entry is bedrock (zelfde provider als kolom-default).
        await set_subscription_pool(s, PK, [
            PoolEntry(provider="bedrock", model=None, drempel=0.9),
        ])
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        await s.commit()

        await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        held_back = await get_card(s, cid)

    # End-to-end: de gate (die de resolver volgt) ziet bedrock als
    # effective provider, ziet dat die gepauzeerd is, en houdt de
    # kaart tegen — ook al geeft de pool-picker bedrock terug als
    # laatste-val-terug-fallback.
    assert transport.calls == []
    assert held_back.column == "Backlog"
    assert held_back.claimed_by is None


@pytest.mark.asyncio
async def test_manual_pause_holds_back_queued_memory_retry(project_with_agents, monkeypatch):
    """A card that was queued before the operator paused a subscription
    must NOT spawn on the next memory-available retry tick. FCR-blokkade:
    the previous ``_retry_queued_cards`` only checked column caps before
    calling ``_run_card`` and let queued cards through."""
    # Pool snapshots are no-ops; the pool router falls through to the
    # "first entry" branch — sufficient to drive the precedence chain.
    from app.kanban import dispatch_pause
    from app.services.scheduling.pending_queue import pending_queue
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        cid = await _make_card(s, title="queued-card", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        # Queue the card via the in-memory pending_queue (singleton).
        pending_queue.enqueue(
            card_id=cid, project_key=PK,
            project_path=project_with_agents,
        )
        # Pause anthropic AFTER the card was queued.
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await s.commit()

        # Force the queue to consider it retryable (the singleton has
        # its own retry_interval; bypass via the deque path).
        pending_queue.dequeue(cid)
        pending_queue.enqueue(
            card_id=cid, project_key=PK,
            project_path=project_with_agents,
        )
        # Patch get_retryable_cards to ignore retry_interval.
        import unittest.mock as mock

        def _force_retryable():
            return list(pending_queue._queue.values())

        with mock.patch.object(
            pending_queue, "get_retryable_cards", _force_retryable,
        ), mock.patch.object(
            dispatch, "get_memory_status_cached",
            return_value=SimpleNamespace(
                is_critical=False, usage_percent=0.4,
            ),
        ):
            await dispatch._retry_queued_cards(transport)
        await s.commit()
        retried = await get_card(s, cid)

    assert transport.calls == [], (
        "queued card must not spawn while its effective provider is "
        "manually paused"
    )
    assert retried.column == "Backlog"
    assert retried.claimed_by is None


@pytest.mark.asyncio
async def test_bulk_orphan_redispatch_respects_manual_pause(project_with_agents):
    """The bulk 'Redispatch all' path must honour the operator's
    subscription pause. Bulk = no per-card operator intent, so the gate
    is consistent with the auto-tick + dispatch_all + memory-retry
    paths. The single-card ``redispatch_card`` (Card drawer) is an
    explicit per-card action and stays unguarded by design."""
    from app.kanban import dispatch_pause

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        # Orphan: unclaimed card on an agent column.
        orphan_id = await _make_card(s, title="orphaned", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=orphan_id, payload={"agent": "engineer"},
        )
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await s.commit()

        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        not_retried = await get_card(s, orphan_id)

    assert results == []
    assert transport.calls == []
    assert not_retried.claimed_by is None


@pytest.fixture
def project_with_analyst(tmp_path):
    """Project with engineer + analyst persona files, mirroring the real repo
    layout, so a work_type='analysis' card resolves to the analyst column."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("engineer", "analyst"):
        (agents_dir / f"{name}.md").write_text(f"# {name}")
    return str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_target_column_applies_work_type_fallback(project_with_analyst):
    """The cap gate (`_resolve_target_column`) resolves a card whose `agent` is
    a CLI id via the work_type fallback — the same way the spawn path
    (`_phase_target_agent`) does. A work_type='analysis' card with
    agent='claude-code' must resolve to 'analyst', not the hardcoded
    'engineer' fallback."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="analyse-me", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "claude-code", "work_type": "analysis"},
        )
        await s.flush()
        card = await get_card(s, cid)
        col = await dispatch._resolve_target_column(
            s, card, project_path=project_with_analyst, project_key=PK,
        )
    assert col == "analyst"


@pytest.mark.asyncio
async def test_analysis_card_gated_against_analyst_not_engineer(project_with_analyst):
    """Regression: a work_type='analysis' card whose `agent` is a CLI id
    ('claude-code', not a persona file) must be gated against its *real* target
    column (analyst) — the column the spawn resolves via the work_type
    fallback — not the hardcoded 'engineer' fallback. A saturated engineer
    column must not starve it while the analyst column still has room.

    Before the fix, `_resolve_target_column` dropped the work_type fallback and
    mis-resolved the card to 'engineer'; with engineer at its cap the card was
    skipped every tick and the analyst never picked it up."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="analyst",
                                     default_agent="analyst", max_sessions=2)
        # Saturate the engineer column with a live agent claim.
        busy_id = await _make_card(s, title="eng-busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy_id, payload={"claimed_by": "agent:k-eng-0001"},
        )
        # An analysis card carrying a CLI id in `agent` (as real cards do when
        # created with an explicit agent='claude-code').
        cid = await _make_card(s, title="analyse-me", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "claude-code", "work_type": "analysis"},
        )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_analyst,
            transport=transport,
        )
        await s.commit()

        moved = await get_card(s, cid)

    # The full engineer column did not block it; it was dispatched to analyst.
    assert result is not None
    assert len(transport.calls) == 1
    assert moved.column == "analyst"
    assert (moved.claimed_by or "").startswith("agent:")


@pytest.mark.asyncio
async def test_column_max_sessions_column_roundtrip():
    """max_sessions on a column can be set via create_column and read back."""
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="testcol",
                                     default_agent="test", max_sessions=3)
        await s.commit()
        cols = await service.list_columns(s, PK)
    matching = [c for c in cols if c.name == "testcol"]
    assert len(matching) == 1
    assert matching[0].max_sessions == 3


@pytest.mark.asyncio
async def test_column_max_sessions_can_be_updated():
    """max_sessions on a column can be updated."""
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key=PK, name="testcol",
                                           default_agent="test", max_sessions=1)
        await s.commit()
        cid = col.id
    async with KanbanSessionLocal() as s:
        updated = await service.update_column(s, cid, max_sessions=5)
        await s.commit()
    assert updated.max_sessions == 5


# ---- retry queue ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_queued_cards_drains_every_dispatchable_card(monkeypatch):
    """Without a per-project session cap, _retry_queued_cards dispatches every
    dispatchable queued card in one tick; per-column caps (when set) are the only
    structural limit at the retry path."""
    from types import SimpleNamespace

    import app.kanban.db as kdb
    import app.services.scheduling.pending_queue as pq_mod
    from app.services.scheduling.pending_queue import PendingQueue

    fresh_queue = PendingQueue()
    monkeypatch.setattr(pq_mod, "pending_queue", fresh_queue)
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(
        dispatch, "get_memory_status_cached",
        lambda: SimpleNamespace(is_critical=False, usage_percent=0.1),
    )

    transport = RecordingTransport()
    ids = []
    async with KanbanSessionLocal() as s:
        for i in range(4):
            ids.append(await _make_card(s, title=f"q{i}", column="Backlog"))
        await s.commit()

    for cid in ids:
        fresh_queue.enqueue(card_id=cid, project_key=PK, project_path="/p")

    await dispatch._retry_queued_cards(transport)

    assert len(transport.calls) == 4
    assert fresh_queue.size == 0


@pytest.mark.asyncio
async def test_retry_queued_cards_dispatches_queued_orphan(monkeypatch):
    # An orphan (unclaimed card left behind in an agent column, see the orphan
    # fallback in _next_card) that got memory-queued must be retried like any
    # other queued card, not silently dropped for not being in "Backlog".
    from types import SimpleNamespace

    import app.kanban.db as kdb
    import app.services.scheduling.pending_queue as pq_mod
    from app.services.scheduling.pending_queue import PendingQueue

    fresh_queue = PendingQueue()
    monkeypatch.setattr(pq_mod, "pending_queue", fresh_queue)
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(
        dispatch, "get_memory_status_cached",
        lambda: SimpleNamespace(is_critical=False, usage_percent=0.1),
    )

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        await s.commit()

    fresh_queue.enqueue(card_id=orphan, project_key=PK, project_path="/p")

    await dispatch._retry_queued_cards(transport)

    assert len(transport.calls) == 1
    assert fresh_queue.size == 0
    async with KanbanSessionLocal() as s:
        card = await get_card(s, orphan)
    assert card.claimed_by is not None


@pytest.mark.asyncio
async def test_get_default_transport_defaults_to_worktree():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_default_transport(s, PK) == "worktree"


@pytest.mark.asyncio
async def test_set_then_get_default_transport_roundtrips(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
        assert await dispatch.get_default_transport(s, PK) == "sandcastle"


@pytest.mark.asyncio
async def test_set_default_transport_rejects_unknown():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_default_transport(s, PK, "podman")


@pytest.mark.asyncio
async def test_get_transport_for_project_uses_meta_sandcastle(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda p: PK
    )
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_get_transport_for_project_defaults_worktree(monkeypatch):
    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda p: PK
    )
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is not dispatch.sandcastle_transport  # a worktree transport callable


# ---- risk_class-driven dispatch defaults ----------------------------------


def test_skip_permissions_for_risk_class_only_meta_stays_permissive():
    # meta (and the no-profile fallback) keep the historical bypass...
    assert dispatch._skip_permissions_for_risk_class("meta") is True
    assert dispatch._skip_permissions_for_risk_class(None) is True
    # ...every product/untrusted class enforces permissions.
    assert dispatch._skip_permissions_for_risk_class("product-staging") is False
    assert dispatch._skip_permissions_for_risk_class("product-prod") is False
    assert dispatch._skip_permissions_for_risk_class("untrusted") is False


def test_transport_for_risk_class_products_default_to_sandcastle():
    assert dispatch._transport_for_risk_class("meta") == "worktree"
    assert dispatch._transport_for_risk_class(None) == "worktree"
    assert dispatch._transport_for_risk_class("product-staging") == "sandcastle"
    assert dispatch._transport_for_risk_class("untrusted") == "sandcastle"


@pytest.mark.asyncio
async def test_get_skip_permissions_product_project_defaults_to_false(monkeypatch):
    async def _risk(project_key):
        return "product-staging"
    monkeypatch.setattr(dispatch, "_project_risk_class", _risk)
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_skip_permissions(s, PK) is False


@pytest.mark.asyncio
async def test_get_skip_permissions_meta_project_stays_true(monkeypatch):
    async def _risk(project_key):
        return None  # no security profile -> meta/legacy default
    monkeypatch.setattr(dispatch, "_project_risk_class", _risk)
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_skip_permissions(s, PK) is True


@pytest.mark.asyncio
async def test_get_skip_permissions_explicit_override_wins(monkeypatch):
    async def _risk(project_key):
        return "product-staging"  # would default to False without an override
    monkeypatch.setattr(dispatch, "_project_risk_class", _risk)
    async with KanbanSessionLocal() as s:
        # Explicit KanbanMeta override to True beats the product-safe default.
        await dispatch.set_skip_permissions(s, PK, True)
        await s.commit()
        assert await dispatch.get_skip_permissions(s, PK) is True


@pytest.mark.asyncio
async def test_get_default_transport_product_project_defaults_to_sandcastle(monkeypatch):
    async def _risk(project_key):
        return "product-staging"
    monkeypatch.setattr(dispatch, "_project_risk_class", _risk)
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_default_transport(s, PK) == "sandcastle"


@pytest.mark.asyncio
async def test_get_default_transport_explicit_override_wins(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)

    async def _risk(project_key):
        return "product-staging"  # would default to sandcastle
    monkeypatch.setattr(dispatch, "_project_risk_class", _risk)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "worktree")
        await s.commit()
        assert await dispatch.get_default_transport(s, PK) == "worktree"


def test_resolve_project_secrets_reads_store_as_env(monkeypatch):
    class FakeStore:
        def list(self, project_key):
            return ["API_TOKEN", "DB_URL"]

        def get(self, project_key, name):
            return {"API_TOKEN": "t0k", "DB_URL": "sqlite://"}.get(name)

    monkeypatch.setattr(dispatch, "_secret_store", lambda: FakeStore())
    assert dispatch._resolve_project_secrets(PK) == {
        "API_TOKEN": "t0k",
        "DB_URL": "sqlite://",
    }


def test_resolve_project_secrets_swallows_store_errors(monkeypatch):
    class BrokenStore:
        def list(self, project_key):
            raise RuntimeError("no passphrase configured")

    monkeypatch.setattr(dispatch, "_secret_store", lambda: BrokenStore())
    # A misconfigured store must never break dispatch — empty dict, no raise.
    assert dispatch._resolve_project_secrets(PK) == {}
    assert dispatch._resolve_project_secrets(None) == {}


@pytest.mark.asyncio
async def test_dispatch_no_todo_cards_is_a_noop():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="done", column="Done")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dispatch_skips_already_claimed_todo_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="claimed")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "someone@else"},
        )
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_spawn_failure_releases_and_returns_card_to_todo():
    # dispatch_project no longer propagates a synchronous spawn failure --
    # it already applied the compensating ops itself and moves on to the
    # next candidate (kaart 05592c13…, "spawn-fout op één kaart breekt de
    # hele dispatch-tick af"). With no other candidate here, the while loop
    # simply ends and dispatch_project returns without raising.
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "Backlog"       # compensated back
    assert card.claimed_by is None        # claim released
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_spawn_failure_on_one_card_does_not_block_the_next_candidate():
    """A synchronous spawn failure on one candidate must not abort the rest
    of dispatch_project's ``while True`` loop -- a healthy second candidate
    in the same project still gets dispatched in the same tick (kaart
    05592c13…, "spawn-fout op één kaart breekt de hele dispatch-tick af").
    Before the fix, _run_card's already-compensated exception unwound the
    whole loop and no other card in the project got a chance that tick;
    since "To Resume" sorts before "Backlog" in _DISPATCH_COLUMNS, a single
    unresumable card could starve every Backlog card for a full tick."""
    class SelectiveFailTransport(RecordingTransport):
        def __init__(self, failing_card_id):
            super().__init__()
            self.failing_card_id = failing_card_id

        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("card_id") == self.failing_card_id:
                raise RuntimeError("Could not resolve project directory for 'x'")
            return {"session_name": kwargs["session_name"]}

    async with KanbanSessionLocal() as s:
        failing_cid = await _make_card(s, title="always-fails", column="To Resume")
        healthy_cid = await _make_card(s, title="healthy", column="Backlog")
        await s.commit()

    transport = SelectiveFailTransport(failing_cid)
    async with KanbanSessionLocal() as s:
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        failing_card = await get_card(s, failing_cid)
        healthy_card = await get_card(s, healthy_cid)

    assert len(transport.calls) == 2, "the healthy card must still be attempted"
    assert failing_card.column == "To Resume"    # compensated back
    assert failing_card.claimed_by is None
    assert failing_card.dispatch_failures == 1
    assert healthy_card.claimed_by is not None   # still dispatched this tick
    assert result is not None and result["card_id"] == healthy_cid


# ---- stale-claim reaping (tmux-liveness) ----------------------------------


@pytest.mark.asyncio
async def test_orphaned_agent_column_card_redispatched_when_cap_has_room():
    # A card left unclaimed in an agent column (e.g. by a prior reap whose dead
    # session had no resumable transcript, see reap_stale_claims) must not be
    # stranded forever: with a free cap slot and nothing waiting in Backlog/To
    # Resume, the tick must pick it back up itself -- otherwise auto-dispatch
    # silently stalls until a human notices and hits "redispatch" by hand.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, orphan)
    assert result is not None
    assert len(transport.calls) == 1
    assert card.claimed_by is not None and card.claimed_by.startswith("agent:")


@pytest.mark.asyncio
async def test_orphaned_agent_column_card_is_dispatched_before_backlog_cards():
    """With both a fresh Backlog card and a leftover orphan available and no cap
    in the way, both are dispatched in one tick — the orphan first.

    Reversed from the original Backlog-first assertion. That order treated a
    Backlog card as higher-value "new work", which contradicted the To-Resume-
    over-Backlog policy this dispatcher already adopted (see
    `test_dispatch_prefers_to_resume_over_backlog`) and, on a capped column,
    starved the orphan outright — see
    `test_orphan_wins_its_own_capped_column_slot_over_backlog` and the
    `_next_card` docstring. The tier order is now one principle: in-flight work
    before new work.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        orphan_card = await get_card(s, orphan)
        waiting_card = await get_card(s, waiting)
    assert orphan_card.claimed_by is not None    # orphan wins the priority
    assert waiting_card.claimed_by is not None   # Backlog card also dispatched this tick
    # Orphan must be picked before the Backlog card in this tick.
    assert len(transport.calls) == 2
    assert "orphaned" in transport.calls[0]["session_name"]
    assert "waiting" in transport.calls[1]["session_name"]


@pytest.mark.asyncio
async def test_orphan_wins_its_own_capped_column_slot_over_backlog(project_with_agents):
    """A capped column's free slot goes to the orphan already sitting in it, not
    to a fresh Backlog card targeting that same column.

    Regression for the starvation observed live on card d0531c12… (2026-08-06):
    `_next_card` walks `_DISPATCH_COLUMNS` fully before it ever reaches the
    orphan fallback, so with `max_sessions=1` a Backlog card claimed the single
    engineer slot every tick and the orphan was skipped by the cap check
    immediately afterwards. The orphan sat unclaimed in `engineer` for a full
    day with `held_reason=None` — invisible, and re-starved on every tick
    (twice within five minutes in the captured logs).

    Only a *capped* column starves: the uncapped case dispatches both cards in
    one tick, Backlog first, which
    `test_orphaned_agent_column_card_waits_for_backlog_cards_first` pins.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        orphan = await _make_card(s, title="orphaned", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=orphan, payload={"agent": "engineer"},
        )
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=waiting, payload={"agent": "engineer"},
        )
        await s.commit()

        await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()
        orphan_card = await get_card(s, orphan)
        waiting_card = await get_card(s, waiting)

    assert len(transport.calls) == 1, (
        "cap=1 must yield exactly one spawn; got "
        f"{[c['session_name'] for c in transport.calls]}"
    )
    assert "orphaned" in transport.calls[0]["session_name"], (
        "the single engineer slot must go to the in-flight orphan, not to new "
        f"Backlog work; spawned {transport.calls[0]['session_name']!r}"
    )
    assert orphan_card.claimed_by is not None
    assert waiting_card.claimed_by is None   # Backlog card waits for the next tick
    assert waiting_card.column == "Backlog"


@pytest.mark.asyncio
async def test_reaper_ignores_human_ui_claims():
    # A human-claimed (me@ui) Doing card is never reaped, even with no live sessions.
    async with KanbanSessionLocal() as s:
        human = await _make_card(s, title="human WIP", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=human, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, human)
    assert reaped == 0
    assert card.claimed_by == "me@ui"


@pytest.mark.asyncio
async def test_reaper_spares_live_sandcastle_claim_without_tmux():
    # A sandcastle-dispatched card has no tmux session, so tmux liveness can never
    # vouch for it. As long as its sandcastle run is active (its session name is in
    # sandcastle_live), the claim must NOT be reaped — otherwise the auto-dispatcher
    # releases and re-spawns it every tick.
    async with KanbanSessionLocal() as s:
        sc = await _make_card(s, title="sandcastle WIP", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=sc, payload={"claimed_by": "agent:k-sc-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live={"k-sc-0001"},
        )
        await s.commit()
        card = await get_card(s, sc)
    assert reaped == 0
    assert card.claimed_by == "agent:k-sc-0001"


@pytest.mark.asyncio
async def test_reaper_reaps_dead_sandcastle_claim():
    # When the sandcastle run is gone (not in sandcastle_live) and there is no tmux
    # session either, the stale claim is reaped like any other dead session.
    async with KanbanSessionLocal() as s:
        sc = await _make_card(s, title="sandcastle dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=sc, payload={"claimed_by": "agent:k-sc-dead"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
        )
        await s.commit()
        card = await get_card(s, sc)
    assert reaped == 1
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_reaper_spares_live_headless_claim_without_tmux():
    # Regression guard for the dispatch-loop documented in
    # docs/cockpit/headless-stream-json-transport-spike.md §5: a headless run has
    # no tmux session AND no SandcastleRun row, so neither of the two original
    # liveness sources can vouch for it. Without the third source (the
    # _live_headless_sessions set plumbed into the reaper), the reaper would
    # release + re-dispatch the card every tick — exactly the sandcastle bug
    # the new sibling was introduced to prevent.
    async with KanbanSessionLocal() as s:
        hl = await _make_card(s, title="headless WIP", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=hl, payload={"claimed_by": "agent:k-hl-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
            headless_live={"k-hl-0001"},
        )
        await s.commit()
        card = await get_card(s, hl)
    assert reaped == 0
    assert card.claimed_by == "agent:k-hl-0001"


@pytest.mark.asyncio
async def test_reaper_reaps_dead_headless_claim():
    # Same shape as test_reaper_reaps_dead_sandcastle_claim: when the headless
    # subprocess is gone (not in headless_live) AND no tmux session AND no
    # sandcastle row, the stale claim is reaped.
    async with KanbanSessionLocal() as s:
        hl = await _make_card(s, title="headless dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=hl, payload={"claimed_by": "agent:k-hl-dead"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
            headless_live=set(),
        )
        await s.commit()
        card = await get_card(s, hl)
    assert reaped == 1
    assert card.claimed_by is None


# ---- transport selection ---------------------------------------------------


def test_default_transport_accepts_headless():
    # The TRANSPORTS tuple must include "headless" so it can be set as a
    # per-project default via KanbanMeta. Unknown values fall back to the
    # default ("worktree") — the legacy contract.
    import asyncio

    from app.kanban.models import KanbanMeta

    async def _check():
        async with KanbanSessionLocal() as s:
            s.add(KanbanMeta(key=dispatch.TRANSPORT_PREFIX + PK, value="headless"))
            await s.commit()
            return await dispatch.get_default_transport(s, PK)

    assert asyncio.run(_check()) == "headless"


def test_default_transport_falls_back_on_unknown_value():
    # Regression guard for the TRANSPORTS-tuple expansion: an unknown value
    # in the meta row silently falls back to the project default rather
    # than raising — the legacy contract that lets an operator recover from a
    # bad value without a DB migration.
    import asyncio

    from app.kanban.models import KanbanMeta

    async def _check():
        async with KanbanSessionLocal() as s:
            s.add(KanbanMeta(key=dispatch.TRANSPORT_PREFIX + PK, value="garbage"))
            await s.commit()
            return await dispatch.get_default_transport(s, PK)

    assert asyncio.run(_check()) == "worktree"  # DEFAULT_TRANSPORT


def test_get_transport_for_card_headless():
    # A card with transport="headless" resolves to headless_transport;
    # a card without it falls through to the project default. Resume
    # priority is preserved (the resume check happens first).
    from app.kanban.headless_runner import headless_transport

    card_hl = KanbanCard(transport="headless", project_key=PK)
    assert dispatch.get_transport_for_card(card_hl, default_transport=RecordingTransport()) is headless_transport

    card_default = KanbanCard(transport=None, project_key=PK)
    fallback = RecordingTransport()
    assert dispatch.get_transport_for_card(card_default, default_transport=fallback) is fallback

    # Resume still wins over an explicit transport= (legacy contract).
    card_resume = KanbanCard(
        transport="headless", project_key=PK,
        resume_session_id="resume-1", resume_project_folder="-home-x-y",
    )
    chosen = dispatch.get_transport_for_card(card_resume, default_transport=fallback)
    # Resume transports are unique per (session_id, folder); assert it's NOT
    # the headless one (any non-headless transport is fine — that's the contract).
    assert chosen is not headless_transport


def test_live_sessions_parses_names(monkeypatch):
    import app.kanban.dispatch as d

    class R:
        returncode = 0
        stdout = "k-a-1\nk-b-2\n"
        stderr = ""
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == {"k-a-1", "k-b-2"}


def test_live_sessions_empty_set_when_no_server(monkeypatch):
    import app.kanban.dispatch as d

    class R:
        returncode = 1
        stdout = ""
        stderr = "no server running on /tmp/tmux-1000/default"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == set()


def test_live_sessions_none_on_ambiguous_tmux_error(monkeypatch):
    # An ambiguous failure must yield None (skip reaping), never an empty set,
    # so a transient tmux hiccup can't release live claims.
    import app.kanban.dispatch as d

    class R:
        returncode = 2
        stdout = ""
        stderr = "tmux: unexpected error talking to server"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() is None


def test_live_sessions_none_when_tmux_missing(monkeypatch):
    import app.kanban.dispatch as d

    def boom(*a, **k):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(d.subprocess, "run", boom)
    assert d._live_sessions() is None


def test_live_sessions_empty_set_on_tmux_3_6_no_server_wording(monkeypatch):
    # tmux 3.6 dropped the "no server running" wording in favour of a generic
    # "error connecting to <socket> (No such file or directory)" message for the
    # exact same "no server ever started" case. This must still map to an empty
    # set, not None, or the reaper/session-recovery permanently refuses to touch
    # any claim whenever no tmux server has been started yet on this host.
    import app.kanban.dispatch as d

    class R:
        returncode = 1
        stdout = ""
        stderr = "error connecting to /tmp/tmux-1000/default (No such file or directory)"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == set()


# ---- project_key -> local path matching -----------------------------------

def test_match_project_paths_maps_enabled_keys_to_local_paths():
    keys = {"git:h/a", "git:h/b"}
    paths = ["/x/a", "/x/b", "/x/c"]
    fake_key_of = {"/x/a": "git:h/a", "/x/b": "git:h/b", "/x/c": "git:h/c"}.get
    out = dispatch.match_project_paths(keys, paths, key_of=fake_key_of)
    assert out == {"git:h/a": "/x/a", "git:h/b": "/x/b"}


@pytest.mark.asyncio
async def test_dispatch_picks_analysis_with_analyst_persona(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("You are the Analyst.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key=PK, entity_id=cid, payload={"agent": "analyst"})
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "You are the Analyst." in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_provider_id_falls_back_to_engineer(tmp_path):
    """card.agent = provider ID (e.g. 'mimo-code') must not create a non-existent column."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text("You are the Engineer.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Task", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key=PK, entity_id=cid, payload={"agent": "mimo-code"})
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    card = await get_card(s, cid)
    assert card.column == "engineer"
    assert "You are the Engineer." in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_prefers_todo_over_analysis(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="A-card", column="Backlog")
        await _make_card(s, title="T-card", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "A-card" in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_injects_ship_mode(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await _make_card(s, title="T-card", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert "Ship mode: direct" in t.calls[0]["prompt"]


def test_worktree_transport_creates_from_origin_master(monkeypatch, tmp_path):
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    captured = {}

    def fake_spawn(cli_id, options, session_name=None, **kwargs):
        captured["cli"] = cli_id
        captured["options"] = options
        captured["session_name"] = session_name
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "app.services.runs.spawn.spawn_session", fake_spawn)

    res = d.worktree_transport(
        directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    fetches = [c for c in ran if "fetch" in c]
    adds = [c for c in ran if "worktree" in c and "add" in c]
    assert fetches and adds
    assert "origin/master" in adds[0]
    opts = captured["options"]
    assert opts.mode == "plain"
    assert opts.skip_permissions is True
    assert opts.repo_path == str(tmp_path)
    assert opts.worktree_path == opts.directory
    assert res["session_name"] == "k-proj-abcd"


def test_worktree_transport_does_not_place_mcp_json_in_worktree(monkeypatch, tmp_path):
    """Route 2 (kaart ``3672c073…``): the worktree transport must NOT copy the
    repo-root ``.mcp.json`` into the worktree.

    Copying it in left the worktree permanently dirty for an external
    product-project (where ``.mcp.json`` is untracked): ``git ls-files
    --others --exclude-standard`` reported it, which (a) made the ship gate
    refuse to run and (b) risked ``git add -A && git commit`` writing Cockpit's
    ``Authorization: Bearer`` token into the customer's git history. The
    dispatched agent still gets its MCP — but via ``--mcp-config`` pointing at
    the repo-root copy (``SpawnCommandOptions.repo_path``), not a worktree file.
    So a fresh worktree here must stay clean.
    """
    import os
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        # Materialise the worktree directory the real git worktree add would
        # have created, so we can assert nothing was written into it.
        if "worktree" in cmd and "add" in cmd:
            wt_path = cmd[cmd.index("add") + 3]
            os.makedirs(wt_path, exist_ok=True)
        class R:
            returncode = 0
            stderr = ""
        return R()

    captured = {}

    def fake_spawn(cli_id, options, session_name=None, **kwargs):
        captured["options"] = options
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr("app.services.runs.spawn.spawn_session", fake_spawn)

    # Repo-root has an untracked .mcp.json (the canonical external-project
    # state after POST /enable).
    mcp_payload = (
        '{"mcpServers": {"cockpit-kanban": {"type": "sse", '
        '"url": "http://localhost:8000/kanban-mcp/sse"}}}'
    )
    (tmp_path / ".mcp.json").write_text(mcp_payload, encoding="utf-8")

    d.worktree_transport(
        directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    adds = [c for c in ran if "worktree" in c and "add" in c]
    assert adds, "expected a git worktree add call"
    wt_path = adds[0][adds[0].index("add") + 3]
    assert not (Path(wt_path) / ".mcp.json").exists(), (
        "worktree transport must not materialise an untracked .mcp.json in the "
        "worktree — that dirties it and can leak the API token via ship"
    )

    # The MCP config still reaches the agent: repo_path is threaded so
    # build_spawn_command can point --mcp-config at the repo-root copy.
    opts = captured["options"]
    assert opts.repo_path == str(tmp_path), (
        f"worktree transport must pass repo_path so the MCP fallback works; "
        f"got {opts.repo_path!r}"
    )


def test_copy_repo_mcp_json_helper_is_gone():
    """The file-copy helper was the rejected route 1; it must not come back —
    a regression that re-adds it would re-introduce the dirty-worktree /
    token-leak bug (kaart ``3672c073…``)."""
    import app.kanban.dispatch as d

    assert not hasattr(d, "_copy_repo_mcp_json_to_worktree"), (
        "route 1 (copy .mcp.json into the worktree) was rejected — the MCP "
        "config must be reached via --mcp-config + repo_path, not a copied file"
    )


def test_worktree_transport_removes_worktree_when_spawn_fails(monkeypatch, tmp_path):
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    def fake_spawn(cli_id, options, session_name=None, **kwargs):
        raise RuntimeError("tmux exploded")

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr("app.services.runs.spawn.spawn_session", fake_spawn)

    with pytest.raises(RuntimeError):
        d.worktree_transport(
            directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    removes = [c for c in ran if "worktree" in c and "remove" in c]
    assert removes, "expected the orphaned worktree to be removed on spawn failure"


def test_mint_session_name_fits_tmux_sanitizer_limit():
    # a long project name must still yield a <=20-char session name, otherwise the
    # tmux-bridge sanitizer truncates it and cleanup/claimant labels diverge.
    name = dispatch._mint_session_name("/home/me/a-very-long-repository-name-here")
    assert len(name) <= 20
    assert name.startswith("k-")


def test_mint_session_name_uses_card_title():
    # Card title should be used for clarity when available.
    name = dispatch._mint_session_name("/home/me/project", card_title="Fix login bug")
    assert len(name) <= 20
    assert name.startswith("k-")
    assert "fix-login" in name


def test_mint_session_name_falls_back_to_project_path():
    # When no card title, project path should be used as before.
    name = dispatch._mint_session_name("/home/me/my-project")
    assert len(name) <= 20
    assert "my-project" in name


def test_mint_session_name_avoids_collision_with_live_tmux_session(monkeypatch):
    # If the minted name happens to already be a running tmux session,
    # spawn_session's own collision fallback (runs.spawn._session_name_for)
    # silently renames the *actual* tmux session -- but the kanban claim, git
    # worktree and git branch were already committed under the original name.
    # cleanup_session_for_card then looks up a tmux session that never existed
    # under that name, assumes the agent "already exited", and releases the
    # claim -- orphaning the real, still-running tmux session forever. Minting
    # must therefore never hand out a name that's already live when the caller
    # has a fresh liveness snapshot.
    import itertools
    import uuid as uuid_mod

    colliding_hex = "aaaa"
    free_hex = "bbbb"
    fake_hexes = itertools.chain([colliding_hex, free_hex], itertools.repeat(free_hex))

    class FakeUUID:
        def __init__(self, hex_val):
            self.hex = hex_val

    monkeypatch.setattr(
        uuid_mod, "uuid4", lambda: FakeUUID(next(fake_hexes))
    )

    name = dispatch._mint_session_name(
        "/home/me/proj", live_sessions={f"k-proj-{colliding_hex}"},
    )

    assert name != f"k-proj-{colliding_hex}"
    assert name == f"k-proj-{free_hex}"


def test_mint_session_name_skips_collision_check_when_live_sessions_unknown(monkeypatch):
    # live_sessions=None (the default) means "no snapshot" -- e.g. a caller/test
    # that doesn't have a fresh tmux query. Minting must not shell out to tmux
    # itself in that case (that would turn every unit test that mints a session
    # name into an integration test hitting the real tmux binary).
    def boom():
        raise AssertionError("must not query tmux when live_sessions is None")

    monkeypatch.setattr(dispatch, "_live_sessions", boom)

    name = dispatch._mint_session_name("/home/me/proj")
    assert name.startswith("k-proj-")


@pytest.mark.asyncio
async def test_spawn_failure_returns_analysis_card_to_analysis():
    # See test_spawn_failure_releases_and_returns_card_to_todo -- a synchronous
    # spawn failure is compensated inside dispatch_project and no longer
    # propagates out of it.
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "Backlog"      # compensated back to its source column
    assert card.claimed_by is None


# ---- redispatch: human override for stuck cards ----------------------------

@pytest.mark.asyncio
async def test_redispatch_releases_claim_and_respawns():
    """Re-dispatch a claimed card: release old claim, spawn new session."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.claimed_by.startswith("agent:")
    assert card.claimed_by != "agent:k-old-0001"  # new session
    assert card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redispatch_unclaimed_card_dispatches_normally():
    """Re-dispatch an unclaimed card (e.g., after stale reaping) works like normal dispatch."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="orphan", column="engineer")
        # No claim - card was reaped
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.claimed_by.startswith("agent:")
    assert card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redispatch_with_agent_override():
    """Re-dispatch with a different agent moves card to new agent's column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="testing",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.column == "testing"
    assert card.claimed_by.startswith("agent:")


@pytest.mark.asyncio
async def test_redispatch_resumes_non_claude_session_instead_of_fresh():
    """A non-Claude card with a dead resumable session must keep its CLI and
    resume target instead of discarding context in a fresh worktree."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    resume_calls = []

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        resume_calls.append((session_name, cli_id))
        return {"session_name": session_name}

    fresh_transport = RecordingTransport()

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_provider": "opencode-go"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-limited-0001"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target",
        return_value=("sess-resumed", "/p/.claude/worktrees/k-limited-0001"),
    ) as resolve_mock, mock.patch.object(
        dispatch, "make_resume_transport", return_value=resume_transport,
    ), mock.patch.object(
        dispatch, "_kill_agent_session", return_value=None,
    ) as kill_mock:
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=fresh_transport,
            )
            await s.commit()
            card = await get_card(s, cid)

    assert result is not None
    assert len(resume_calls) == 1
    assert resume_calls[0][1] == "open-code"
    assert fresh_transport.calls == []  # never fell back to a fresh session
    assert card.resume_session_id == "sess-resumed"
    assert card.resume_project_folder == "/p/.claude/worktrees/k-limited-0001"
    resolve_mock.assert_called_once_with(
        "/p", "k-limited-0001", cli_id="open-code",
    )
    kill_mock.assert_called_once_with("k-limited-0001")


@pytest.mark.asyncio
async def test_redispatch_no_resumable_transcript_falls_back_to_fresh_session():
    """When the old session's worktree has no resumable transcript, redispatch still
    falls back to a fresh session (existing behaviour)."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0002"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=None,
    ):
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()
            card = await get_card(s, cid)

    assert result is not None
    assert len(transport.calls) == 1
    assert card.resume_session_id is None


@pytest.mark.asyncio
async def test_redispatch_returns_none_for_missing_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id="nonexistent", project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_reaper_spares_live_session_regardless_of_mcp_state(monkeypatch):
    """Regression for [self-improve] MCP-serverdisconnect → claim-release.

    The reaper's liveness sources are tmux (worktree-transport), SandcastleRun
    rows (sandcastle-transport), and the headless subprocess registry
    (headless-transport). None of them reads the kanban MCP-server connection
    state — and that is intentional: a brief MCP disconnect from a session's
    Claude CLI says nothing about whether the underlying CLI process is still
    productive. An MCP disconnect is therefore NOT a liveness signal: as long
    as tmux lists the session, the claim MUST be preserved.

    Locks in the invariant observed in the b00f3705… incident (Lemma-analyse,
    2026-07-21T19:17:22): a session's MCP-server connection briefly dropped,
    the underlying tmux session stayed alive, and a claim-release + redispatch
    was triggered anyway. The release turned out not to originate in the reaper
    (it doesn't read MCP state) — but this test guards against any future
    code path that wires MCP connection state into claim-release.
    """
    import unittest.mock as mock

    async with KanbanSessionLocal() as s:
        live_card = await _make_card(s, title="mcp-disconnected WIP", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=live_card,
            payload={"claimed_by": "agent:k-product-analy-312c"},
        )
        await s.commit()

    # The "MCP-disconnect" is encoded by NOT removing the session from
    # live_sessions — its alive status in tmux is independent of MCP state.
    # Mock subprocess.run so `_live_sessions()` returns the session even
    # when no real tmux server exists in the test environment.
    class _R:
        returncode = 0
        stdout = "k-product-analy-312c\n"
        stderr = ""
    with mock.patch.object(dispatch.subprocess, "run", lambda *a, **k: _R()):
        async with KanbanSessionLocal() as s:
            reaped = await dispatch.reap_stale_claims(
                s, project_key=PK,
                cards=await list_cards(s, PK),
                live_sessions=dispatch._live_sessions(),
                sandcastle_live=set(),
                headless_live=set(),
            )
            await s.commit()
            card = await get_card(s, live_card)
    assert reaped == 0, (
        "reaper must NOT release a claim whose session is alive in tmux — "
        "MCP-server connection state is not a liveness source."
    )
    assert card.claimed_by == "agent:k-product-analy-312c"


@pytest.mark.asyncio
async def test_redispatch_kills_live_session_posts_audit_comment(monkeypatch):
    """When redispatch_card is invoked on a card whose tmux session is still
    alive, the activity feed MUST show a `**Note:**` audit comment so an
    operator can distinguish "redispatch over a long-dead session" from
    "redispatch over a still-productive session".

    Closes the visibility gap behind the b00f3705… incident: a card's
    `release_without_terminal_move` counter stayed at 0 (redispatch bypasses
    `release_card_claim` by design — see its docstring), so the activity
    feed showed no visible signal that a live session had been killed. The
    audit comment makes the state change traceable on the board itself.
    """
    import unittest.mock as mock

    from app.kanban import session_recovery

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="still-productive", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-product-analy-312c"},
        )
        await s.commit()

    # Mock the two external interactions so the test is hermetic:
    #   - `_live_sessions()` reports 312c as still alive in tmux
    #   - `_resolve_resume_target()` returns no transcript (force fresh spawn)
    #   - `_kill_agent_session` is observed but does nothing on disk
    class _R:
        returncode = 0
        stdout = "k-product-analy-312c\n"
        stderr = ""
    with mock.patch.object(dispatch.subprocess, "run", lambda *a, **k: _R()), \
         mock.patch.object(session_recovery, "_resolve_resume_target", return_value=None), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()
            activity = await service.card_activity(s, cid)

    note = [op for op in activity
            if op.op_type == "comment"
            and (op.payload or {}).get("text", "").startswith("**Note:**")]
    # Two Note comments are now expected since the caller_source feature: one
    # for the live-session kill (this test's contract) and one labelling the
    # redispatch entry-point. Both are intentional, so assert on the live-
    # kill Note by its content rather than a brittle count.
    kill_notes = [op for op in note
                  if "k-product-analy-312c" in (op.payload or {}).get("text", "")]
    assert len(kill_notes) == 1, (
        "redispatch over a still-alive tmux session must post exactly one "
        "live-kill **Note:** audit comment; "
        f"got {len(kill_notes)} Note(s) containing the session name."
    )
    text = kill_notes[0].payload["text"]
    assert "k-product-analy-312c" in text
    assert "still alive" in text or "MCP" in text


@pytest.mark.asyncio
async def test_redispatch_over_live_session_audit_names_automatic_recovery_not_operator():
    """The live-session kill audit comment must not misattribute the restart.

    Regression for the reopened [self-improve] card 4ed4edb9. The prior fix's
    audit text hardcoded an operator narrative ("the operator / an explicit
    redispatch call chose to restart anyway"). When the caller is automatic
    startup session-recovery (`recover_interrupted_sessions`), no operator chose
    anything — the snapshot the recovery pass acted on simply went stale. If the
    comment still blames an operator, the activity feed points a future debugger
    at the wrong actor, which is exactly the documentation defect that reopened
    this card. Assert the automatic-recovery caller gets an accurate attribution.
    """
    import unittest.mock as mock

    from app.kanban import session_recovery

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="still-productive", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-product-analy-312c"},
        )
        await s.commit()

    class _R:
        returncode = 0
        stdout = "k-product-analy-312c\n"
        stderr = ""
    with mock.patch.object(dispatch.subprocess, "run", lambda *a, **k: _R()), \
         mock.patch.object(session_recovery, "_resolve_resume_target", return_value=None), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
                caller_source="recover_interrupted_sessions",
            )
            await s.commit()
            activity = await service.card_activity(s, cid)

    kill_notes = [op for op in activity
                  if op.op_type == "comment"
                  and (op.payload or {}).get("text", "").startswith("**Note:**")
                  and "k-product-analy-312c" in (op.payload or {}).get("text", "")]
    assert len(kill_notes) == 1, (
        "redispatch over a live session must post exactly one live-kill Note; "
        f"got {len(kill_notes)}."
    )
    text = kill_notes[0].payload["text"]
    assert "automatic startup session-recovery" in text
    assert "No operator chose to restart" in text
    assert "the operator / an explicit redispatch call chose to restart" not in text


@pytest.mark.asyncio
async def test_redispatch_posts_caller_source_audit_comment():
    """Regression for [self-improve] Redispatch-trigger-bron onzichtbaar in activity-feed.

    Every redispatch_card invocation MUST post a `**Note:** Redispatched via <source>`
    audit comment on the card so the activity feed tells an operator (without extra
    grep-werk) which entry-point fired: REST UI (`ui`), MCP-tool (`mcp:<session_id>`),
    `redispatch_all_orphans` (`bulk_orphans`), or `recover_project`
    (`recover_interrupted_sessions`). The string-conventions-prefix `**Note:**`
    matches the existing live-session-kill audit comment so they style uniformly.
    """
    from app.kanban.service import card_activity

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="ui-redispatch", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-source-aaaa"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            caller_source="ui",
        )
        await s.commit()
        activity = await card_activity(s, cid)

    assert result is not None, "redispatch must succeed even without live tmux session"

    # The audit comment is a **Note:** comment (matching the existing
    # live-session-kill Note convention) and includes the source verbatim so
    # operators can grep for it. Must be exactly one such comment — the
    # caller-source Note must not collide with or duplicate the live-kill Note
    # (which only fires when the prior tmux session is still alive in this test
    # it isn't).
    note = [op for op in activity
            if op.op_type == "comment"
            and (op.payload or {}).get("text", "").startswith("**Note:**")]
    source_notes = [op for op in note
                    if "Redispatched via" in (op.payload or {}).get("text", "")]
    assert len(source_notes) == 1, (
        "redispatch_card must post exactly one `**Note:** Redispatched via "
        f"<source>` audit comment when caller_source is supplied; got {len(source_notes)}."
    )
    text = source_notes[0].payload["text"]
    assert "ui" in text, f"caller_source label must appear verbatim in audit comment; got {text!r}"


@pytest.mark.asyncio
async def test_redispatch_defaults_caller_source_to_unspecified():
    """When called without caller_source, redispatch_card must still post an
    audit comment (so the activity feed never silently loses the trace) and
    must label the source `unspecified` — distinguishing legacy callers from
    the three current entry-points that already pass an explicit label.
    Keeps the function back-compat-safe without forcing every internal caller
    to be retrofitted in the same commit.
    """
    from app.kanban.service import card_activity

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="legacy-redispatch", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-legacy-bbbb"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        # No caller_source kwarg — mirrors the in-process callers
        # (session_recovery.recover_project, existing tests) that don't know
        # about the new label yet.
        await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await card_activity(s, cid)

    source_notes = [op for op in activity
                    if op.op_type == "comment"
                    and "Redispatched via" in (op.payload or {}).get("text", "")]
    assert len(source_notes) == 1, (
        "legacy callers must still produce exactly one source-audit comment "
        f"(with `unspecified` label); got {len(source_notes)}."
    )
    assert "unspecified" in source_notes[0].payload["text"]


@pytest.mark.asyncio
async def test_redispatch_all_orphans_tags_every_redispatch_with_bulk_source():
    """`redispatch_all_orphans` must tag each redispatch it triggers with the
    `bulk_orphans` source label so a future operator investigating "who
    redispatched card X" can see it came from the bulk path — not from the
    REST UI or an MCP-tool call.
    """
    from app.kanban.service import card_activity

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid1 = await _make_card(s, title="orphan-bulk-1", column="developer")
        cid2 = await _make_card(s, title="orphan-bulk-2", column="testing")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        cids = [cid1, cid2]
        activities = [(c, await card_activity(s, c)) for c in cids]

    assert len(results) == 2
    for cid, activity in activities:
        source_notes = [op for op in activity
                        if op.op_type == "comment"
                        and "Redispatched via" in (op.payload or {}).get("text", "")]
        assert len(source_notes) == 1, (
            f"card {cid} must have exactly one source-audit comment from bulk_orphans; "
            f"got {len(source_notes)}."
        )
        assert "bulk_orphans" in source_notes[0].payload["text"], (
            f"bulk redispatch must label source `bulk_orphans`; got {source_notes[0].payload['text']!r}"
        )


@pytest.mark.asyncio
async def test_mcp_redispatch_card_wrapper_forwards_mcp_source_label():
    """End-to-end through the MCP-tool wrapper: invoking the registered
    `mcp_server.redispatch_card` async function must leave a
    `**Note:** Redispatched via `mcp`` audit comment on the card.

    Closes the FCR-flagged test-coverage gap from kaart 57785696c9444c1ba539b438a3666e76:
    the audit-comment unit test exercises `dispatch.redispatch_card` directly,
    but a regression in `mcp_server.redispatch_card` (e.g. a typo that strips
    the `caller_source` kwarg) would not be caught without this end-to-end
    coverage. Follows the same direct-import pattern that
    `test_kanban_done_summary.py` and `test_kanban_mcp.py` already use for
    MCP-tool wrappers — skipping `mcp.tool()` registration avoids importing
    the full FastMCP server, which needs a long-lived transport.
    """
    import unittest.mock as mock

    from app.kanban import mcp_server as m
    from app.kanban.service import card_activity

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="mcp-wrapper-e2e", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-mcp-wrapp-7"},
        )
        await s.commit()

    # Mock `_live_sessions` to None so the live-kill Note branch is *not*
    # triggered — keeps the assertion focused on exactly one `**Note:**
    # Redispatched via <source>` comment (the wrapper's caller_source label).
    # Mock `_run_card` so the spawn side (which would otherwise try
    # `git fetch origin` on `/p`, an invalid path) is skipped. The audit
    # comment is posted *before* `_run_card` is called inside
    # `dispatch.redispatch_card`, so mocking `_run_card` lets us assert on
    # the comment without standing up a real transport.
    async def fake_run_card(*args, **kwargs):
        return {"session_name": "k-mcp-fake-0001", "tmux_target": "k-mcp-fake-0001:0.0"}

    with mock.patch.object(dispatch, "_live_sessions", return_value=None), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None), \
         mock.patch.object(dispatch, "_run_card", side_effect=fake_run_card):
        result = await m.redispatch_card(
            card_id=cid, project_path="/p", agent=None,
        )
    # The wrapper returns a {ok, card_id, session_name} dict; check we got
    # the canonical success shape before reading the activity feed.
    assert result.get("ok") is True, (
        f"MCP wrapper returned a non-OK response: {result!r}"
    )
    assert result["card_id"] == cid

    # Activity feed must have the `mcp` source label, not `unspecified`.
    async with KanbanSessionLocal() as s:
        activity = await card_activity(s, cid)

    source_notes = [op for op in activity
                    if op.op_type == "comment"
                    and "Redispatched via" in (op.payload or {}).get("text", "")]
    assert len(source_notes) == 1, (
        "MCP end-to-end must post exactly one source-audit comment; "
        f"got {len(source_notes)}."
    )
    assert "mcp" in source_notes[0].payload["text"]
    assert "unspecified" not in source_notes[0].payload["text"], (
        "MCP wrapper must propagate its `mcp` label to dispatch.redispatch_card; "
        f"got {source_notes[0].payload['text']!r}"
    )


@pytest.mark.asyncio
async def test_rest_redispatch_card_endpoint_forwards_ui_source_label():
    """End-to-end through the REST handler: POST /cards/{cid}/redispatch from
    the operator's UI must leave a `**Note:** Redispatched via `ui`` audit
    comment on the card.

    Closes the same FCR-flagged gap as the MCP end-to-end test: verifying the
    REST wiring actually forwards `payload.caller_source or "ui"` through the
    handler to `dispatch.redispatch_card` instead of silently dropping it.
    Uses `httpx.AsyncClient` against the FastAPI ASGI app — the same pattern
    as `test_kanban_done_summary._client`.
    """
    import unittest.mock as mock

    from httpx import ASGITransport, AsyncClient

    from app.kanban.service import card_activity
    from app.main import app

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="rest-handler-e2e", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-rest-handl-9"},
        )
        await s.commit()

    # Mock `_live_sessions` so the live-kill branch doesn't fire alongside
    # the source audit comment. Mock `_run_card` so the worktree-transport
    # `git fetch origin` on `/p` is bypassed (the audit comment is posted
    # *before* `_run_card`, so this doesn't affect the assertion).
    async def fake_run_card(*args, **kwargs):
        return {"session_name": "k-rest-fake-0001", "tmux_target": "k-rest-fake-0001:0.0"}

    with mock.patch.object(dispatch, "_live_sessions", return_value=None), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None), \
         mock.patch.object(dispatch, "_run_card", side_effect=fake_run_card):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.post(
                f"/api/v1/kanban/cards/{cid}/redispatch",
                json={"project_path": "/p", "agent": None},
            )

    assert r.status_code == 200, (
        f"REST redispatch endpoint must succeed for a live-claimed card; "
        f"got {r.status_code} {r.text!r}"
    )

    async with KanbanSessionLocal() as s:
        activity = await card_activity(s, cid)

    source_notes = [op for op in activity
                    if op.op_type == "comment"
                    and "Redispatched via" in (op.payload or {}).get("text", "")]
    assert len(source_notes) == 1, (
        f"REST end-to-end must post exactly one source-audit comment; "
        f"got {len(source_notes)}."
    )
    assert "ui" in source_notes[0].payload["text"], (
        "REST handler default must propagate `ui` caller_source when the "
        f"request omits the optional caller_source field; got {source_notes[0].payload['text']!r}"
    )
    assert "unspecified" not in source_notes[0].payload["text"], (
        "REST handler must NOT pass through the unspecified default — "
        f"its canonical label is `ui`; got {source_notes[0].payload['text']!r}"
    )


@pytest.mark.asyncio
async def test_redispatch_all_orphans():
    """Batch redispatch: all unclaimed cards on agent columns get dispatched."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Two orphaned cards on agent columns (unclaimed)
        await _make_card(s, title="orphan1", column="developer")
        await _make_card(s, title="orphan2", column="testing")
        # One card that's fine (claimed)
        claimed = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "agent:k-alive-0001"},
        )
        # One card on Backlog (not orphaned)
        await _make_card(s, title="backlog", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 2
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_redispatch_all_no_orphans():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Only claimed cards
        claimed = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert len(results) == 0
    assert transport.calls == []


# ---- dispatch_all_pending: batch dispatch from Backlog ---------------------

@pytest.mark.asyncio
async def test_dispatch_all_pending():
    """Batch dispatch: all unclaimed Backlog cards get dispatched."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="card1", column="Backlog")
        await _make_card(s, title="card2", column="Backlog")
        # claimed card should be skipped
        claimed = await _make_card(s, title="busy", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 2
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_dispatch_all_pending_empty():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Only Done cards
        await _make_card(s, title="done", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert len(results) == 0
    assert transport.calls == []


# ---- count consistency: frontend vs backend vs actual dispatch ------------

from datetime import UTC

from app.kanban.schemas import COLUMNS as _COLUMNS

_FIXED = set(_COLUMNS)


def _frontend_pending_count(cards) -> int:
    return sum(1 for c in cards if c.column in ("Backlog", "To Resume") and not c.claimed_by)


def _frontend_orphan_count(cards) -> int:
    return sum(1 for c in cards if c.column not in _FIXED and not c.claimed_by)


@pytest.mark.asyncio
async def test_pending_count_matches_dispatch_results():
    """Frontend-style pending count = backend list_pending_cards = dispatch_all_pending results."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        await _make_card(s, title="b", column="Backlog")
        await _make_card(s, title="c", column="Backlog")
        busy = await _make_card(s, title="busy", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "me@ui"},
        )
        await _make_card(s, title="done", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        fe = _frontend_pending_count(cards)
        backend = len(await service.list_pending_cards(s, PK))
        assert fe == 3, f"frontend pending={fe}"
        assert backend == 3, f"backend pending={backend}"
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3, f"dispatched={len(results)}"


@pytest.mark.asyncio
async def test_list_pending_cards_includes_unclaimed_to_resume():
    """To Resume cards (unclaimed, tagged for resume) are dispatch candidates too —
    not just Backlog. Dispatch all must not silently skip them."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="backlog-card", column="Backlog")
        await _make_card(s, title="resumable-card", column="To Resume")
        claimed = await _make_card(s, title="claimed-resume", column="To Resume")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        pending = await service.list_pending_cards(s, PK)
    assert {c.title for c in pending} == {"backlog-card", "resumable-card"}


@pytest.mark.asyncio
async def test_dispatch_all_pending_resumes_to_resume_cards():
    """dispatch_all_pending must dispatch unclaimed To Resume cards through the
    resume transport (their recorded resume_session_id), not the default/fresh one."""
    import unittest.mock as mock

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        resumable = await _make_card(s, title="resumable", column="To Resume")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=resumable,
            payload={"resume_session_id": "sess-abc", "resume_project_folder": "proj-folder"},
        )
        await s.commit()

    resume_calls = []

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        resume_calls.append(session_name)
        return {"session_name": session_name}

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            results = await dispatch.dispatch_all_pending(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    assert len(results) == 2
    assert len(resume_calls) == 1        # the To Resume card resumed its session
    assert len(transport.calls) == 1     # the plain Backlog card used the default transport


@pytest.mark.asyncio
async def test_orphan_count_matches_redispatch_results():
    """Frontend-style orphan count = backend list_orphaned_cards = redispatch_all_orphans results."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="o1", column="engineer")
        await _make_card(s, title="o2", column="testing")
        await _make_card(s, title="o3", column="code-review")
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await _make_card(s, title="backlog", column="Backlog")
        await _make_card(s, title="blocked", column="Impediment")
        await _make_card(s, title="done2", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        fe = _frontend_orphan_count(cards)
        backend = len(await service.list_orphaned_cards(s, PK))
        assert fe == 3, f"frontend orphans={fe}"
        assert backend == 3, f"backend orphans={backend}"
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3, f"redispatched={len(results)}"


@pytest.mark.asyncio
async def test_frontend_backend_claimed_by_unanimity():
    """Frontend `!c.claimed_by` and backend `claimed_by.is_(None)` must agree on all valid states."""
    async with KanbanSessionLocal() as s:
        c_unclaimed = await _make_card(s, title="none", column="engineer")
        c_human = await _make_card(s, title="human", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c_human, payload={"claimed_by": "me@ui"},
        )
        c_agent = await _make_card(s, title="agented", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c_agent, payload={"claimed_by": "agent:k-test-0001"},
        )
        await s.commit()

        card_none = await get_card(s, c_unclaimed)
        card_human = await get_card(s, c_human)
        card_agent = await get_card(s, c_agent)

        assert card_none.claimed_by is None
        assert not card_none.claimed_by

        assert card_human.claimed_by == "me@ui"
        assert card_human.claimed_by

        assert card_agent.claimed_by == "agent:k-test-0001"
        assert card_agent.claimed_by

        cards = await list_cards(s, PK)
        fe_orphans = _frontend_orphan_count(cards)
        be_orphans = len(await service.list_orphaned_cards(s, PK))
        assert fe_orphans == 1
        assert be_orphans == 1


@pytest.mark.asyncio
async def test_empty_string_claimed_by_causes_mismatch():
    """claimed_by='' (empty string): frontend treats as unclaimed, backend treats as claimed."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="bogus", column="engineer")
        await s.commit()

        from sqlalchemy import update

        from app.kanban.models import KanbanCard as KCModel
        await s.execute(
            update(KCModel).where(KCModel.id == cid).values(claimed_by="")
        )
        await s.commit()

        card = await get_card(s, cid)
        assert card.claimed_by == ""
        assert not card.claimed_by

        cards = await list_cards(s, PK)
        fe_orphans = _frontend_orphan_count(cards)
        be_orphans = len(await service.list_orphaned_cards(s, PK))
        assert fe_orphans == 1, f"frontend sees {fe_orphans} orphans"
        assert be_orphans == 0, f"backend sees {be_orphans} orphans"

        fe_pending = _frontend_pending_count(cards)
        be_pending = len(await service.list_pending_cards(s, PK))
        assert fe_pending == 0
        assert be_pending == 0


@pytest.mark.asyncio
async def test_dispatch_all_remaining_count_is_correct():
    """After dispatch_all_pending, remaining cards still show correct counts."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        await _make_card(s, title="b", column="Backlog")
        await _make_card(s, title="orphan", column="engineer")
        busy = await _make_card(s, title="claimed", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await _make_card(s, title="blocked", column="Impediment")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        assert len(results) == 2

        cards = await list_cards(s, PK)
        assert _frontend_pending_count(cards) == 0
        assert _frontend_orphan_count(cards) == 1
        assert len(await service.list_pending_cards(s, PK)) == 0
        assert len(await service.list_orphaned_cards(s, PK)) == 1


@pytest.mark.asyncio
async def test_redispatch_all_remaining_count_is_correct():
    """After redispatch_all_orphans, remaining cards still show correct counts."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="o1", column="engineer")
        await _make_card(s, title="o2", column="testing")
        await _make_card(s, title="pending", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        assert len(results) == 2

        cards = await list_cards(s, PK)
        assert _frontend_orphan_count(cards) == 0
        assert _frontend_pending_count(cards) == 1
        assert len(await service.list_orphaned_cards(s, PK)) == 0
        assert len(await service.list_pending_cards(s, PK)) == 1


# ---- depends_on gate on bulk dispatch paths -------------------------------

@pytest.mark.asyncio
async def test_dispatch_all_pending_skips_blocked_card():
    """A Backlog card whose depends_on points to a non-Done parent must NOT be
    spawned by dispatch_all_pending — same predicate the auto-dispatch tick uses.
    Without this gate, the bulk action silently contradicts the Blocked badge."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Backlog")
        # Child whose only dep is the still-Open parent.
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        # A second, unblocked card that should still go through.
        await _make_card(s, title="free", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(results) == 2, f"expected only unblocked cards dispatched, got {results}"
    # The blocked child must not appear in the spawned set; the free card and
    # the (Open but unblocked) parent do.
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_redispatch_all_orphans_skips_blocked_card():
    """An orphaned card whose depends_on points to a non-Done parent must NOT be
    spawned by redispatch_all_orphans. Mirrors the dispatch_all_pending contract."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Parent on Backlog (still Open). Child on an agent column, unclaimed
        # → an "orphan" eligible for redispatch_all_orphans — but blocked on the
        # parent via depends_on.
        parent = await _make_card(s, title="parent", column="Backlog")
        blocked_orphan = await _make_card(s, title="blocked-orphan", column="developer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=blocked_orphan, payload={"depends_on": [parent]},
        )
        # An orphan with no deps must still go through.
        await _make_card(s, title="free-orphan", column="testing")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(results) == 1, f"expected only unblocked orphan dispatched, got {results}"
    assert len(transport.calls) == 1


# ---- dangling depends_on: dispatch tick surfaces to Impediment ------------

@pytest.mark.asyncio
async def test_dispatch_project_moves_dangling_dep_card_to_impediment():
    """A Backlog card whose depends_on names a card id that no longer exists on
    the board must NOT be silently held forever by the fail-closed dep gate.
    The tick moves it to Impediment with an actionable `**Dangling dependency:**`
    comment + the red `error` label, so the permanent block is visible. This is
    the runtime seam from docs/cockpit/dangling-depends-on-analyse.md §1.1/§4."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        blocked = await _make_card(s, title="dangling", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=blocked, payload={"depends_on": ["does-not-exist-0000"]},
        )
        # A healthy card that must still dispatch this same tick.
        await _make_card(s, title="free", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, blocked)
        activity = await service.card_activity(s, blocked)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert card.column == "Impediment"
    assert dispatch.ERROR_LABEL in (card.labels or [])
    assert any(t.startswith(dispatch.DANGLING_DEP_COMMENT_PREFIX) for t in comment_texts)
    assert any("does-not-exist-0000" in t for t in comment_texts)
    # Only the healthy "free" card was actually dispatched.
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_project_silently_skips_live_not_done_dep():
    """A Backlog card whose dep is a live-but-not-Done sibling is *healthy*
    waiting — it must stay on Backlog, NOT be moved to Impediment and NOT get a
    dangling-dependency comment. Only a dep that resolves to no card at all is
    surfaced; this guards against false Impediment moves for normal blocking."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Backlog")
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, child)
        activity = await service.card_activity(s, child)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    # Still on Backlog (healthy blocked), no Impediment move, no dangling comment.
    assert card.column == "Backlog"
    assert dispatch.ERROR_LABEL not in (card.labels or [])
    assert not any(
        t.startswith(dispatch.DANGLING_DEP_COMMENT_PREFIX) for t in comment_texts
    )


@pytest.mark.asyncio
async def test_dispatch_project_ignores_cross_project_dep_as_not_dangling():
    """A dep pointing at a live card in ANOTHER project is not dangling (it
    exists board-wide), so it must not be flagged to Impediment — it just stays
    a healthy silent skip. Prevents a false Impediment move for cross-project
    deps, matching sweep_dangling_depends_on.py's board-wide existence check."""
    transport = RecordingTransport()
    other_pk = "git:example.com/me/other"
    async with KanbanSessionLocal() as s:
        # A live card in a different project.
        cross = await apply_operation(
            s, op_type="create", entity_type="card", project_key=other_pk,
            entity_id=None, payload={"title": "cross", "column": "Backlog"},
        )
        await s.flush()
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [cross]},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, child)
    # Not dangling (cross-project card exists) → healthy skip, stays on Backlog.
    assert card.column == "Backlog"
    assert dispatch.ERROR_LABEL not in (card.labels or [])


# ---- priority sort on bulk dispatch paths ---------------------------------

def _dispatch_order(transport) -> list[str]:
    """Extract the order in which the recording transport was invoked, by
    matching each call's prompt against card titles (build_card_prompt embeds
    the title verbatim). Returns the title sequence in dispatch order."""
    out = []
    for call in transport.calls:
        prompt = call["prompt"]
        for title in ("urgent", "medium-card", "low-card", "card-a", "card-b",
                      "card-c", "orphan-high", "orphan-mid", "orphan-low",
                      "resume-low", "backlog-high"):
            if title in prompt:
                out.append(title)
                break
    return out


@pytest.mark.asyncio
async def test_dispatch_all_pending_dispatches_high_priority_first():
    """dispatch_all_pending sorts by priority desc (high → medium → low) before
    the per-card loop, so the manual "Dispatch All" button no longer falls back
    to rank FIFO when an operator tags urgent work."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Insert in rank order low → high → medium so a FIFO implementation
        # would dispatch in that order. The fix must reorder them.
        await _make_card(s, title="low-card", column="Backlog", priority="low")
        await _make_card(s, title="urgent", column="Backlog", priority="high")
        await _make_card(s, title="medium-card", column="Backlog", priority="medium")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3
    assert _dispatch_order(transport) == ["urgent", "medium-card", "low-card"]


@pytest.mark.asyncio
async def test_dispatch_all_pending_preserves_rank_within_same_priority():
    """Stable sort on rank: within the same priority, older (lower-rank) cards
    still dispatch first — the fix must not scramble the existing tie-break."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Two high-priority cards (a created first, b created second) and one low
        await _make_card(s, title="card-a", column="Backlog", priority="high")
        await _make_card(s, title="card-b", column="Backlog", priority="high")
        await _make_card(s, title="card-c", column="Backlog", priority="low")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    order = _dispatch_order(transport)
    # High first (in rank order: a, b), then low
    assert order == ["card-a", "card-b", "card-c"]


@pytest.mark.asyncio
async def test_redispatch_all_orphans_dispatches_high_priority_first():
    """redispatch_all_orphans sorts orphans by priority desc, matching the
    auto-tick's _next_card behaviour."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Insert in rank order low → high → medium so a FIFO implementation
        # would dispatch in that order.
        await _make_card(s, title="orphan-low", column="developer", priority="low")
        await _make_card(s, title="orphan-high", column="testing", priority="high")
        await _make_card(s, title="orphan-mid", column="review", priority="medium")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3
    assert _dispatch_order(transport) == ["orphan-high", "orphan-mid", "orphan-low"]


# ---- dispatch column order: To Resume drains before Backlog ----------------
#
# The order of `_DISPATCH_COLUMNS` is the policy "finish interrupted work before
# starting new work". It used to be ("Backlog", "To Resume") purely because that
# was the literal order of the tuple in the initial commit — no test pinned it,
# so the intended policy could silently flip back on any edit. These tests pin it
# on all three call sites: the tuple itself, the auto-tick, and "Dispatch All".


def test_dispatch_columns_order_puts_to_resume_first():
    """Pin the tuple order itself: `_next_card` returns the first column that
    yields a selectable card, so this order *is* the policy. Membership-only
    assertions elsewhere (test_inception, test_dispatch_gate) don't catch a flip."""
    assert dispatch._DISPATCH_COLUMNS == ("To Resume", "Backlog")


def test_dispatch_order_key_ranks_column_above_priority():
    """Column beats priority, matching `_next_card`'s column-then-priority walk:
    a low-priority To Resume card outranks a high-priority Backlog card, and an
    orphan (column outside the tuple) sorts last."""
    resume_low = KanbanCard(column="To Resume", priority="low", project_key=PK)
    backlog_high = KanbanCard(column="Backlog", priority="high", project_key=PK)
    orphan_high = KanbanCard(column="developer", priority="high", project_key=PK)
    ordered = sorted([backlog_high, orphan_high, resume_low],
                     key=dispatch._dispatch_order_key)
    assert [c.column for c in ordered] == ["To Resume", "Backlog", "developer"]


@pytest.mark.asyncio
async def test_tick_dispatches_to_resume_before_backlog():
    """The auto-tick picks the To Resume card first even though the Backlog card
    is higher priority and was created first — interrupted work owns a worktree
    and a resumable transcript, both of which decay while it waits."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="backlog-high", column="Backlog", priority="high")
        await _make_card(s, title="resume-low", column="To Resume", priority="low")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert _dispatch_order(transport)[0] == "resume-low"


@pytest.mark.asyncio
async def test_dispatch_all_pending_dispatches_to_resume_before_backlog():
    """"Dispatch All" follows the same column order as the tick. Before
    `_dispatch_order_key`, this path sorted by priority across both columns and
    would have dispatched the high-priority Backlog card first — the bulk path
    silently contradicting the auto-tick."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="backlog-high", column="Backlog", priority="high")
        await _make_card(s, title="resume-low", column="To Resume", priority="low")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 2
    assert _dispatch_order(transport) == ["resume-low", "backlog-high"]


def test_priority_key_helper_matches_priority_rank():
    """The extracted `_priority_key` helper must produce the same sort key the
    inline `_PRIORITY_RANK.get(c.priority, 0)` did — same numeric rank per
    priority, defaulting to 0 for unknown / None. Guards the helper extraction
    in dispatch.py."""
    class _C:
        def __init__(self, p):
            self.priority = p
    assert dispatch._priority_key(_C("high")) == 3
    assert dispatch._priority_key(_C("medium")) == 2
    assert dispatch._priority_key(_C("low")) == 1
    assert dispatch._priority_key(_C("none")) == 0
    assert dispatch._priority_key(_C(None)) == 0
    assert dispatch._priority_key(_C("garbage")) == 0


@pytest.mark.asyncio
async def test_dispatch_all_pending_picks_up_card_after_dep_clears():
    """After the parent moves to Done, the previously-blocked child becomes
    dispatchable on the next bulk call — confirms the transition is live and
    doesn't require a restart."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Backlog")
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    # First bulk call: parent is unblocked (no deps), child is blocked. Only
    # the parent should dispatch — the Blocked child stays in Backlog.
    async with KanbanSessionLocal() as s:
        results_before = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_before) == 1

    # Move parent to Done → child's deps are now satisfied.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="move", entity_type="card", project_key=PK,
            entity_id=parent, payload={"column": "Done"},
        )
        await s.commit()

    # Second bulk call: child is now dispatchable.
    async with KanbanSessionLocal() as s:
        results_after = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_after) == 1


# ---- regression: dep-gate must require terminal Done, not just "claimed" --
#
# The existing `test_dispatch_all_pending_skips_blocked_card` covers the
# Backlog-Open parent case (parent never picked up). Card 2eaa87166… reported
# the dispatch gate firing for a parent that was *already claimed* and moved
# to an agent column — i.e. mid-flight, not yet terminal. The helper is
# supposed to require `column == "Done"` (the strictest terminal), so this
# pair locks that behaviour in for the bulk dispatch paths explicitly.


@pytest.mark.asyncio
async def test_dispatch_all_pending_skips_child_when_dep_claimed_in_agent_column():
    """A child whose depends_on points to a parent that is *claimed* and
    currently sitting in an agent column (e.g. "engineer") — i.e. mid-flight,
    not yet terminal — must NOT be picked up by ``dispatch_all_pending``.

    Regression for kanban card 2eaa87166…: the dispatcher briefly let a
    frontend child through while its backend parent was in `engineer`. The
    fix is purely on the helper's column check (already ``!= "Done"``); the
    test pins the contract so a future relaxation can't reintroduce the race.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Parent already claimed (simulating a session in flight): column is an
        # agent column, claimed_by is set, claim is fresh. The child lists
        # this parent in depends_on.
        parent = await _make_card(s, title="parent", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=parent, payload={"claimed_by": "agent:some-session"},
        )
        # An unblocked card that should still go through — proves the gate is
        # not over-eager (a "skip everything" test would be vacuous).
        await _make_card(s, title="free", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Create the child with the depends_on AFTER the seed commit so the
        # parent's `claim` op above is durable. (apply_operation's create
        # path is idempotent on existing ids, but the claim guard is
        # conditional on `claimed_by IS NULL`; adding it after the seed
        # commit is the only deterministic order.)
        parent_row = await get_card(s, parent)
        assert parent_row is not None  # sanity: parent exists post-commit
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    # Only the free card dispatches. The child stays in Backlog (its parent
    # is mid-flight in `engineer`, not Done).
    assert len(results) == 1, f"expected only free card dispatched, got {results}"
    assert len(transport.calls) == 1
    # And the child is still where we left it — not silently claimed or moved.
    async with KanbanSessionLocal() as s:
        child_after = await get_card(s, child)
    assert child_after.column == "Backlog"
    assert child_after.claimed_by is None


@pytest.mark.asyncio
async def test_dispatch_all_pending_picks_up_child_after_dep_moves_from_agent_to_done():
    """After the parent transitions from a mid-flight agent column to Done,
    the previously-blocked child becomes dispatchable on the next bulk call.

    Mirrors ``test_dispatch_all_pending_picks_up_card_after_dep_clears`` but
    starts the parent in an agent column (claimed) instead of Backlog — the
    same "dep gate requires strict Done" property, on the transition side.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=parent, payload={"claimed_by": "agent:some-session"},
        )
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    # First bulk call: child is blocked (parent is mid-flight in engineer).
    async with KanbanSessionLocal() as s:
        results_before = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_before) == 0, (
        f"expected child to be blocked while parent is mid-flight, got {results_before}"
    )

    # Parent finishes: agent column → Done. Child's deps are now satisfied.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="move", entity_type="card", project_key=PK,
            entity_id=parent, payload={"column": "Done"},
        )
        await s.commit()

    # Second bulk call: child is now dispatchable.
    async with KanbanSessionLocal() as s:
        results_after = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_after) == 1, (
        f"expected child to dispatch after parent → Done, got {results_after}"
    )


@pytest.mark.asyncio
async def test_dispatch_project_skips_child_when_dep_claimed_in_agent_column():
    """Same regression as ``...skips_child_when_dep_claimed_in_agent_column``
    but on the auto-dispatch tick (``dispatch_project``) — the path the
    card-2eaa87166… bug report actually describes.

    A child whose parent is mid-flight (column=``engineer``, claimed) must
    NOT be picked up by the tick. After the parent transitions agent → Done,
    the next tick picks the child up.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=parent, payload={"claimed_by": "agent:some-session"},
        )
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    # First tick: child is blocked — the tick must NOT dispatch it.
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 0, (
        "child dispatched despite parent being claimed in an agent column"
    )

    # Parent finishes: agent column → Done. Next tick must pick the child up.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="move", entity_type="card", project_key=PK,
            entity_id=parent, payload={"column": "Done"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1, (
        "child did not dispatch after parent moved to Done"
    )


# ---- card transport field persistence ------------------------------------

@pytest.mark.asyncio
async def test_transport_field_persisted_on_create():
    """card.transport set at create time must survive the round-trip."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "sandcastle card", "transport": "sandcastle"},
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.transport == "sandcastle"


@pytest.mark.asyncio
async def test_transport_field_updated_via_update_op():
    """card.transport can be changed after creation via an update op."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"transport": "sandcastle"},
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.transport == "sandcastle"


@pytest.mark.asyncio
async def test_card_transport_sandcastle_uses_sandcastle_transport():
    """A card with transport=sandcastle must use sandcastle_transport when dispatched."""
    worktree = RecordingTransport()
    sc_calls = []

    def fake_sandcastle(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    import unittest.mock as mock
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "sc card", "column": "Backlog", "transport": "sandcastle"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "sandcastle_transport", side_effect=fake_sandcastle):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=worktree,
            )
            await s.commit()

    # sandcastle_transport was called, not the worktree fallback
    assert len(sc_calls) == 1
    assert worktree.calls == []


@pytest.mark.asyncio
async def test_card_transport_worktree_overrides_sandcastle_project_default():
    """A card with transport=worktree uses worktree even when the project default is sandcastle."""
    sc_calls = []
    wt_calls = []

    def fake_sandcastle(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    def fake_worktree(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        wt_calls.append(session_name)
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "worktree card", "column": "Backlog", "transport": "worktree"},
        )
        await s.commit()

    import unittest.mock as mock
    with mock.patch.object(dispatch, "sandcastle_transport", side_effect=fake_sandcastle), \
         mock.patch.object(dispatch, "worktree_transport", side_effect=fake_worktree):
        async with KanbanSessionLocal() as s:
            # project default is sandcastle, but card overrides to worktree
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=fake_sandcastle,
            )
            await s.commit()

    assert sc_calls == []        # sandcastle was NOT called
    assert len(wt_calls) == 1   # worktree WAS called


# ---- resume transport -------------------------------------------------------

def test_make_resume_transport_records_call():
    """make_resume_transport produces a callable that passes session_id through."""
    calls = []

    def fake_spawn(cli_id, options, *, session_name, **kwargs):
        calls.append({"options": options, "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock
    with mock.patch("app.services.runs.spawn.spawn_session", fake_spawn), \
         mock.patch("app.services.scheduling.session_registry.session_registry.can_add_session",
                    return_value=True):
        transport = dispatch.make_resume_transport(
            session_id="abc-123", project_folder="-home-user-repo",
        )
        result = transport(directory="/p", prompt="continue", session_name="k-test-0001")

    assert len(calls) == 1
    opts = calls[0]["options"]
    assert opts.mode == "resume"
    assert opts.session_id == "abc-123"
    assert opts.project_folder == "-home-user-repo"
    assert opts.prompt == "continue"
    assert result == {"session_name": "k-test-0001"}


def test_make_resume_transport_threads_repo_path_for_mcp_fallback():
    """Kaart ``bc123e2d…``: the resume transport must pass ``repo_path`` so a
    resume spawn into a worktree without ``.mcp.json`` still gets
    ``--mcp-config <repo-root>/.mcp.json`` (route 2, kaart ``3672c073…``).

    The dispatcher passes the project_root as the transport's ``directory``
    kwarg (``card_transport(directory=project_path, ...)`` in dispatch.py).
    Without ``repo_path`` threaded through, ``SpawnCommandOptions.repo_path``
    defaults to ``None``, and ``_project_mcp_config_args`` falls back to
    ``--strict-mcp-config`` alone — silently losing ``cockpit-kanban`` MCP
    on the first resume of any external product-project card.
    """
    calls = []

    def fake_spawn(cli_id, options, *, session_name, **kwargs):
        calls.append({"options": options, "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock
    with mock.patch("app.services.runs.spawn.spawn_session", fake_spawn), \
         mock.patch("app.services.scheduling.session_registry.session_registry.can_add_session",
                    return_value=True):
        transport = dispatch.make_resume_transport(
            session_id="abc-123", project_folder="-home-user-repo",
        )
        # The dispatcher's project_root is the transport's ``directory`` arg.
        project_root = "/scratch/scratchpad/product-x"
        transport(directory=project_root, prompt="continue", session_name="k-test-0001")

    assert len(calls) == 1
    opts = calls[0]["options"]
    assert opts.repo_path == project_root, (
        f"resume transport must thread repo_path so the worktree's "
        f"missing .mcp.json falls back to the repo-root copy; got "
        f"opts.repo_path={opts.repo_path!r}"
    )


def test_make_resume_transport_repo_fallback_reaches_spawn_command(
    monkeypatch, tmp_path,
):
    """End-to-end (kaart ``bc123e2d…``): the resume transport's
    ``SpawnCommandOptions`` translate into a ``claude --mcp-config
    <repo-root>/.mcp.json`` argv when the resume cwd (the worktree) lacks
    the file but the repo-root has it.

    The original ``spawn_session`` would call ``cli.resolve_directory`` to
    rewrite ``options.directory`` to the worktree path before invoking
    ``cli.build_spawn_command`` — so we replay that two-step path inside
    the fake ``spawn_session`` here to assert the *final* argv a dispatched
    agent would receive. Worktree precedence is also pinned: when the
    worktree has its own ``.mcp.json``, the launch cwd wins.
    """
    import unittest.mock as mock

    # Layout: repo-root has .mcp.json (external product-project after
    # POST /enable); worktree has none. After the fix, the spawned argv
    # must point --mcp-config at the repo-root copy.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".mcp.json").write_text(
        '{"mcpServers": {"cockpit-kanban": {}}}', encoding="utf-8"
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()

    # resolve_directory looks up the cwd from the transcript; stand in
    # with a fixed worktree path so the fake spawn_session exercises the
    # same `directory` rewrite the real one does.
    from app.services.agentic_cli import claude_code as cc_mod

    monkeypatch.setattr(cc_mod.ClaudeCodeCli, "resolve_directory",
                        lambda self, options: str(worktree))

    captured = {}

    def fake_spawn(cli_id, options, *, session_name, **kwargs):
        # Replay the same two-step the real spawn_session does
        # (claude_code.py:96-102 / spawn.py:192-193): rewrite `directory`
        # via resolve_directory, then build the actual argv.
        cli = cc_mod.ClaudeCodeCli()
        resolved_directory = cli.resolve_directory(options)
        from app.services.agentic_cli.base import SpawnCommandOptions
        resolved_options = SpawnCommandOptions(
            **{**options.__dict__, "directory": resolved_directory},
        )
        captured["argv"] = cli.build_spawn_command(resolved_options)
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    with mock.patch("app.services.runs.spawn.spawn_session", fake_spawn), \
         mock.patch("app.services.scheduling.session_registry.session_registry.can_add_session",
                    return_value=True):
        transport = dispatch.make_resume_transport(
            session_id="abc-123", project_folder="-home-user-repo",
        )
        transport(directory=str(repo_root), prompt="continue",
                  session_name="k-resume-test")

    argv = captured["argv"]
    assert "--mcp-config" in argv, (
        f"resume into a worktree without .mcp.json must fall back to the "
        f"repo-root copy via --mcp-config; argv={argv}"
    )
    idx = argv.index("--mcp-config")
    assert argv[idx + 1] == str(repo_root / ".mcp.json"), (
        f"--mcp-config must point at the repo-root .mcp.json; got {argv[idx + 1]!r}"
    )
    assert "--strict-mcp-config" in argv, (
        f"host ~/.claude.json MCPs would leak into the resume; argv={argv}"
    )
    # Route 1 (file copy into the worktree) was rejected — the fallback must
    # NOT have materialised a file inside the worktree either (kaart 3672c073…).
    assert not (worktree / ".mcp.json").exists(), (
        "resume transport must not copy .mcp.json into the worktree; ship "
        "gate would refuse to run and the API token could leak via commit"
    )


def test_make_resume_transport_prefers_worktree_mcp_over_repo_root(
    monkeypatch, tmp_path,
):
    """Precedence (kaart ``bc123e2d…`` AC4): when the resume cwd (worktree)
    has its own ``.mcp.json`` *and* the repo-root does, the launch cwd
    wins — its copy is the canonical origin/master version (cockpit itself
    tracks the file so every worktree has it; an external product-project
    typically has the worktree empty so this is the cockpit-internal lane).
    The point of this test is to lock the precedence at the resume transport
    layer: a future refactor of ``_project_mcp_config_args`` can't silently
    flip it without the resume path breaking too.
    """
    import unittest.mock as mock

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".mcp.json").write_text(
        '{"mcpServers": {"REPO": {}}}', encoding="utf-8"
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".mcp.json").write_text(
        '{"mcpServers": {"WORKTREE": {}}}', encoding="utf-8"
    )

    from app.services.agentic_cli import claude_code as cc_mod

    monkeypatch.setattr(cc_mod.ClaudeCodeCli, "resolve_directory",
                        lambda self, options: str(worktree))

    captured = {}

    def fake_spawn(cli_id, options, *, session_name, **kwargs):
        cli = cc_mod.ClaudeCodeCli()
        resolved_directory = cli.resolve_directory(options)
        from app.services.agentic_cli.base import SpawnCommandOptions
        resolved_options = SpawnCommandOptions(
            **{**options.__dict__, "directory": resolved_directory},
        )
        captured["argv"] = cli.build_spawn_command(resolved_options)
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    with mock.patch("app.services.runs.spawn.spawn_session", fake_spawn), \
         mock.patch("app.services.scheduling.session_registry.session_registry.can_add_session",
                    return_value=True):
        transport = dispatch.make_resume_transport(
            session_id="abc-123", project_folder="-home-user-repo",
        )
        transport(directory=str(repo_root), prompt="continue",
                  session_name="k-resume-test")

    argv = captured["argv"]
    assert "--mcp-config" in argv, f"argv={argv}"
    idx = argv.index("--mcp-config")
    assert argv[idx + 1] == str(worktree / ".mcp.json"), (
        f"worktree .mcp.json must take precedence over repo-root; "
        f"got {argv[idx + 1]!r}"
    )


# ---- cause-aware spawn-gate message (bevinding 5) --------------------------
#
# All three transports in dispatch.py (worktree, sandcastle, resume) and the
# headless transport must raise ``MemoryLimitExceeded`` with the same
# cause-aware message produced by ``SessionRegistry.build_limit_message``.
# Legacy message blamed memory whenever a counter ceiling was the binding
# constraint — see docs/cockpit/spawn-test-bridge-sessions-analyse.md.


def _fake_tmux_run(live_panes):
    """Stub for ``subprocess.run(["tmux", "list-panes", ...])`` used by
    ``SessionRegistry._live_pane_ids``. Patch ``sreg.subprocess.run`` with
    this rather than a now-removed module-level helper — session_registry.py
    no longer has a standalone tmux-probe function; the probe lives as the
    ``_live_pane_ids`` static method and shells out via ``subprocess.run``
    directly (see the self-healing reconciliation fix it now shares code
    with)."""
    def _run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join(sorted(live_panes)) + ("\n" if live_panes else ""),
            stderr="",
        )
    return _run


def _patch_registry_to_full_counter_ceiling(monkeypatch, *, max_sessions=5,
                                            live_pane_ids=None,
                                            pane_count=5):
    """Make the registry look like a counter ceiling at max_sessions/5 with
    ``pane_count`` zombie/live tmux panes — the bevinding-5 scenario.

    Seeds ``_panes`` directly (bypassing ``record()``) so the leak shape is
    deterministic regardless of the self-healing reconcile's real-time
    throttle window — see the analogous seeding in test_session_registry.py.
    """
    import app.services.scheduling.session_registry as sreg
    reg = sreg.SessionRegistry(max_sessions=max_sessions)
    for i in range(pane_count):
        reg._panes[f"sess-{i}"] = f"%{100 + i}"
    monkeypatch.setattr(sreg, "session_registry", reg)
    if live_pane_ids is not None:
        monkeypatch.setattr(sreg.subprocess, "run", _fake_tmux_run(live_pane_ids))
    return reg


def test_worktree_transport_raises_with_counter_ceiling_message(monkeypatch):
    """Bevinding 5 — the classic case: 5/5 with comfortable memory must NOT
    blame memory. The message must say counter ceiling + slot breakdown.

    Uses live-matching tmux panes (no leak) — this is the honest "genuinely
    full" shape at the transport-integration level. The self-healing
    reconciliation added alongside this card would otherwise clean up a
    seeded phantom-pane leak on the very first ``can_add_session()`` call
    (it's not throttled yet on a freshly built registry), so this level
    can't deterministically demonstrate a leak surviving to the message —
    that diagnostic property is exhaustively covered at the SessionRegistry
    unit level instead (test_limit_message_surfaces_zombie_pane_count)."""
    import app.services.scheduling.session_registry as sreg

    _patch_registry_to_full_counter_ceiling(
        monkeypatch, max_sessions=5, pane_count=5,
        live_pane_ids={f"%{100 + i}" for i in range(5)},
    )
    # Patch the consumer's binding for the memory-status call.
    monkeypatch.setattr(sreg, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.15, available_bytes=13562 * 1024 * 1024,
        is_critical=False, estimated_max_sessions=107,
    ))

    with pytest.raises(MemoryLimitExceeded) as ei:
        dispatch.worktree_transport(
            directory="/tmp/proj", prompt="hi", session_name="k-proj-abcd",
        )

    msg = str(ei.value)
    assert "counter ceiling" in msg
    assert "5/5" in msg
    # Legacy misleading pattern: must NOT appear as cause.
    assert "Memory: 15% used, 13562MB available" not in msg
    # Memory note must explicitly state it's not the binding constraint.
    assert "not the binding constraint" in msg.lower() or "not the binding" in msg.lower()
    # Slot breakdown shows a genuinely full registry — no leak here.
    assert "5 tmux-backed" in msg
    assert "5 live" in msg


def test_sandcastle_transport_raises_with_counter_ceiling_message(monkeypatch):
    """Same shape as the worktree transport — same message.

    No tmux panes involved (sandcastle runs are external reservations), so
    ``_panes`` stays empty and slot_breakdown() never shells out to tmux —
    no tmux fake needed."""
    import app.services.scheduling.session_registry as sreg

    reg = sreg.SessionRegistry(max_sessions=3)
    for i in range(3):
        reg.reserve_external(f"k-external-{i}")
    monkeypatch.setattr(sreg, "session_registry", reg)
    monkeypatch.setattr(sreg, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.20, available_bytes=13000 * 1024 * 1024,
        is_critical=False, estimated_max_sessions=120,
    ))

    with pytest.raises(MemoryLimitExceeded) as ei:
        dispatch.sandcastle_transport(
            directory="/tmp/proj", prompt="hi", session_name="k-proj-abcd",
        )

    msg = str(ei.value)
    assert "counter ceiling" in msg
    assert "3/3" in msg
    assert "3 external" in msg
    assert "not the binding constraint" in msg.lower() or "not the binding" in msg.lower()


def test_sandcastle_transport_injects_project_scoped_secrets(monkeypatch):
    """The sandcastle transport resolves the project's SecretStore secrets and
    forwards them to start_run so they reach the container as env vars.

    risk_class-driven defaults route product/untrusted projects to sandcastle,
    so this is the transport where per-project secret isolation matters most.
    """
    import app.services.scheduling.session_registry as sreg

    reg = sreg.SessionRegistry(max_sessions=10)
    monkeypatch.setattr(sreg, "session_registry", reg)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda d: "proj-A")
    monkeypatch.setattr(
        dispatch, "_resolve_project_secrets",
        lambda pk: {"API_TOKEN": "secret-A"} if pk == "proj-A" else {},
    )

    captured = {}

    class FakeService:
        async def start_run(self, *, project_path, prompt, branch_name, extra_env=None):
            captured["extra_env"] = extra_env
            captured["project_path"] = project_path
            return SimpleNamespace(id=1)

    import app.services.sandcastle_service as scmod
    monkeypatch.setattr(scmod, "sandcastle_service", FakeService())

    # No running loop => the transport runs _start() to completion via asyncio.run.
    result = dispatch.sandcastle_transport(
        directory="/projects/A", prompt="hi", session_name="k-a-abcd",
    )

    assert result["transport"] == "sandcastle"
    assert captured["extra_env"] == {"API_TOKEN": "secret-A"}


def test_sandcastle_transport_secrets_do_not_leak_across_projects(monkeypatch):
    """Project A's secrets never reach project B's spawn — each resolves its own."""
    import app.services.scheduling.session_registry as sreg

    reg = sreg.SessionRegistry(max_sessions=10)
    monkeypatch.setattr(sreg, "session_registry", reg)

    per_project_secrets = {
        "proj-A": {"API_TOKEN": "secret-A"},
        "proj-B": {"API_TOKEN": "secret-B"},
    }
    dir_to_key = {"/projects/A": "proj-A", "/projects/B": "proj-B"}
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda d: dir_to_key[d])
    monkeypatch.setattr(
        dispatch, "_resolve_project_secrets",
        lambda pk: dict(per_project_secrets.get(pk, {})),
    )

    seen = []

    class FakeService:
        async def start_run(self, *, project_path, prompt, branch_name, extra_env=None):
            seen.append((project_path, extra_env))
            return SimpleNamespace(id=len(seen))

    import app.services.sandcastle_service as scmod
    monkeypatch.setattr(scmod, "sandcastle_service", FakeService())

    dispatch.sandcastle_transport(directory="/projects/A", prompt="a", session_name="k-a-0001")
    dispatch.sandcastle_transport(directory="/projects/B", prompt="b", session_name="k-b-0002")

    assert ("/projects/A", {"API_TOKEN": "secret-A"}) in seen
    assert ("/projects/B", {"API_TOKEN": "secret-B"}) in seen
    # No cross-contamination: B's spawn never saw A's token.
    b_env = next(env for path, env in seen if path == "/projects/B")
    assert b_env == {"API_TOKEN": "secret-B"}


def test_resume_transport_reinjects_project_scoped_secrets(monkeypatch):
    """A resume spawns a fresh tmux session with env rebuilt from scratch, so
    project-scoped secrets must be re-injected — same as worktree/sandcastle."""
    import app.services.scheduling.session_registry as sreg

    reg = sreg.SessionRegistry(max_sessions=10)
    monkeypatch.setattr(sreg, "session_registry", reg)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda d: "proj-A")
    monkeypatch.setattr(
        dispatch, "_resolve_project_secrets",
        lambda pk: {"API_TOKEN": "secret-A"} if pk == "proj-A" else {},
    )

    captured = {}

    def fake_spawn_session(cli_id, options, *, session_name=None, project_key=None,
                           runtime=None, extra_env=None, **kwargs):
        captured["extra_env"] = extra_env
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.services.runs.spawn as spawnmod
    monkeypatch.setattr(spawnmod, "spawn_session", fake_spawn_session)

    transport = dispatch.make_resume_transport(
        session_id="abc-123", project_folder="-home-user-repo",
    )
    transport(directory="/projects/A", prompt="continue", session_name="k-a-abcd")

    assert captured["extra_env"] == {"API_TOKEN": "secret-A"}


def test_make_resume_transport_raises_with_counter_ceiling_message(monkeypatch):
    """Resume transport uses the same cause-aware message builder."""
    import app.services.scheduling.session_registry as sreg

    _patch_registry_to_full_counter_ceiling(
        monkeypatch, max_sessions=5, pane_count=5,
        live_pane_ids={f"%{100 + i}" for i in range(5)},
    )
    monkeypatch.setattr(sreg, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.15, available_bytes=13562 * 1024 * 1024,
        is_critical=False, estimated_max_sessions=107,
    ))

    transport = dispatch.make_resume_transport(
        session_id="abc-123", project_folder="-home-user-repo",
    )

    with pytest.raises(MemoryLimitExceeded) as ei:
        transport(directory="/p", prompt="continue", session_name="k-proj-abcd")

    msg = str(ei.value)
    assert "counter ceiling" in msg
    assert "5/5" in msg
    assert "5 live" in msg


def test_worktree_transport_raises_with_memory_ceiling_message(monkeypatch):
    """When memory IS the binding constraint, memory figures stay in the
    message — they're the cause. Counter-ceiling case is the one that
    forbids memory figures as cause.

    Only external reservations, no tmux panes — no tmux fake needed."""
    import app.services.scheduling.session_registry as sreg

    # No override → memory ceiling.
    reg = sreg.SessionRegistry()
    # Fill it with external reservations so the limit trips.
    for i in range(50):
        reg.reserve_external(f"k-ex-{i}")
    monkeypatch.setattr(sreg, "session_registry", reg)
    monkeypatch.setattr(sreg, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.92, available_bytes=1024 * 1024 * 1024,
        is_critical=True, estimated_max_sessions=50,
    ))

    with pytest.raises(MemoryLimitExceeded) as ei:
        dispatch.worktree_transport(
            directory="/tmp/proj", prompt="hi", session_name="k-proj-abcd",
        )

    msg = str(ei.value)
    assert "memory ceiling" in msg
    # Memory pressure figures DO belong here — they're the cause.
    assert "92%" in msg
    assert "1024MB" in msg


@pytest.mark.asyncio
async def test_get_transport_for_card_uses_resume_when_set():
    """A card with resume_session_id gets make_resume_transport, not worktree_transport."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key="",
            entity_id=cid,
            payload={"resume_session_id": "sess-xyz", "resume_project_folder": "-p"},
        )
        await s.flush()
        card = await get_card(s, cid)

    transport = dispatch.get_transport_for_card(card, dispatch.worktree_transport)
    # The returned transport should NOT be the worktree_transport or sandcastle_transport
    assert transport is not dispatch.worktree_transport
    assert transport is not dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_redispatch_with_resume_session_id_uses_resume_transport():
    """When card has resume_session_id, redispatch calls resume transport, not worktree."""
    calls = []

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None, **kwargs):
        calls.append({"mode": "resume", "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit card", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key="",
            entity_id=cid,
            payload={"resume_session_id": "old-sess-id"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-dead"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p",
            )
            await s.commit()

    assert result is not None
    assert len(calls) == 1
    assert calls[0]["mode"] == "resume"


# ---- To Resume column ---------------------------------------------------------


@pytest.mark.asyncio
async def test_move_to_resume_moves_card_to_to_resume():
    """_move_to_resume finds a resumable session, sets resume fields, moves to To Resume,
    kills the tmux session, and releases the claim."""
    import unittest.mock as mock

    cid = None
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0001"},
        )
        await s.commit()

    card = None
    from app.kanban import session_recovery

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        with mock.patch.object(
            dispatch, "_kill_agent_session", return_value=None,
        ) as kill_mock:
            async with KanbanSessionLocal() as s:
                card = await get_card(s, cid)
                result = await dispatch._move_to_resume(
                    s, card=card, project_key=PK, project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert result is True
    assert card is not None
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-abc"
    assert card.resume_project_folder == "proj-folder"
    assert card.claimed_by is None
    kill_mock.assert_called_once_with("k-dead-0001")


@pytest.mark.asyncio
async def test_move_to_resume_routes_detection_to_original_cli():
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="codex context-limit", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"executor_agent_id": "codex-cli"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-codex"},
        )
        await s.commit()

    captured = []

    def fake_resolve(project_path, session_name, **kwargs):
        captured.append((project_path, session_name, kwargs.get("cli_id")))
        return "codex-session", "/p/.claude/worktrees/k-dead-codex"

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", side_effect=fake_resolve,
    ), mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path="/p",
            )
            await s.commit()

    assert result is True
    assert captured == [("/p", "k-dead-codex", "codex-cli")]


@pytest.mark.asyncio
async def test_move_to_resume_sets_scheduled_at_when_provided():
    """_move_to_resume writes an explicit scheduled_at onto the card, so the
    dispatch tick's _is_due check can hold it out of auto-dispatch until then."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit-scheduled", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0005"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                card = await get_card(s, cid)
                result = await dispatch._move_to_resume(
                    s, card=card, project_key=PK, project_path="/p",
                    scheduled_at="2026-07-11T23:10:00+02:00",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert result is True
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


@pytest.mark.asyncio
async def test_move_to_resume_returns_false_when_no_resume_target():
    """_move_to_resume returns False when no resumable transcript is found."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="no-resume", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0002"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=None,
    ):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path="/p",
            )
            await s.commit()

    assert result is False


@pytest.mark.asyncio
async def test_move_to_resume_returns_false_for_fixed_column_card():
    """_move_to_resume returns False immediately for cards already on fixed columns."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="already-done", column="Done")
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path="/p",
            )

    assert result is False


@pytest.mark.asyncio
async def test_reaper_moves_resumable_dead_session_to_to_resume():
    """reap_stale_claims with project_path moves resumable dead sessions to To Resume."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable-dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0003"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-xyz", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                reaped = await dispatch.reap_stale_claims(
                    s, project_key=PK, cards=await list_cards(s, PK),
                    live_sessions=set(), project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert reaped == 1
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-xyz"
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_reaper_move_to_resume_sets_fallback_scheduled_at():
    """With no transcript to read a reset time from, the reaper falls back to
    now + FALLBACK_PAUSE_HOURS so the card doesn't get immediately re-picked up
    by the next dispatch tick while the rate limit is still in effect.

    The dead session's transcript takes precedence when it carries a parseable
    reset time -- see test_reaper_prefers_transcript_reset_time_over_guess."""
    import unittest.mock as mock
    from datetime import UTC, datetime, timedelta

    from app.kanban import session_recovery
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable-dead-fallback", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0006"},
        )
        await s.commit()

    before = datetime.now(UTC)
    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-fb", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                reaped = await dispatch.reap_stale_claims(
                    s, project_key=PK, cards=await list_cards(s, PK),
                    live_sessions=set(), project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)
    after = datetime.now(UTC)

    assert reaped == 1
    assert card.column == "To Resume"
    assert card.scheduled_at is not None
    fire_at = datetime.fromisoformat(card.scheduled_at)
    assert before + timedelta(hours=FALLBACK_PAUSE_HOURS) <= fire_at
    assert fire_at <= after + timedelta(hours=FALLBACK_PAUSE_HOURS)


@pytest.mark.asyncio
async def test_reaper_prefers_transcript_reset_time_over_guess(tmp_path):
    """A dead session's transcript usually still holds the limit message that
    killed it. When that message carries a parseable reset time, the reaper
    must schedule the resume for *that* moment instead of the blind
    now + FALLBACK_PAUSE_HOURS guess -- guessing parks a card for 5h even when
    the real reset is minutes away (or already passed).
    """
    import json
    import unittest.mock as mock
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.kanban import session_recovery

    tz = ZoneInfo("Europe/Brussels")
    reset_day = datetime.now(tz) + timedelta(days=1)
    message = (
        "You've hit your weekly limit · resets "
        f"{reset_day.strftime('%b %-d')}, 7pm (Europe/Brussels)"
    )
    transcript = tmp_path / "sess-tr.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {"role": "assistant", "content": [{"type": "text", "text": message}]},
    }) + "\n")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable-dead-transcript", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0007"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-tr", "proj-folder"),
    ):
        with mock.patch.object(
            session_recovery, "_resolve_transcript_file", return_value=transcript,
        ) as resolve_transcript:
            with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
                async with KanbanSessionLocal() as s:
                    reaped = await dispatch.reap_stale_claims(
                        s, project_key=PK, cards=await list_cards(s, PK),
                        live_sessions=set(), project_path="/p",
                    )
                    await s.commit()
                    card = await get_card(s, cid)

    assert reaped == 1
    assert resolve_transcript.called, "reaper never looked at the transcript"
    assert card.column == "To Resume"
    fire_at = datetime.fromisoformat(card.scheduled_at).astimezone(tz)
    assert (fire_at.month, fire_at.day) == (reset_day.month, reset_day.day)
    assert (fire_at.hour, fire_at.minute) == (19, 0)


@pytest.mark.asyncio
async def test_reaper_without_project_path_plain_release():
    """reap_stale_claims without project_path falls back to plain release for dead sessions."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="plain-dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0004"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-xyz", "proj-folder"),
    ):
        async with KanbanSessionLocal() as s:
            reaped = await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions=set(),
                # project_path not set — should NOT call _move_to_resume
            )
            await s.commit()
            card = await get_card(s, cid)

    assert reaped == 1
    assert card.column == "engineer"  # NOT moved to To Resume
    assert card.resume_session_id is None  # resume fields NOT set
    assert card.claimed_by is None


# ---- move_limited_session_to_resume (live session hit its usage limit) -----

@pytest.mark.asyncio
async def test_move_limited_session_to_resume_moves_matching_card(monkeypatch):
    """A Notification hook event for a live, limit-hit session moves its card to
    To Resume and kills the (still alive) tmux session, same as the dead-session
    reaper does for a crashed one."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None) as kill_mock:
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-live"
    assert card.claimed_by is None
    kill_mock.assert_called_once_with("k-live-0001")


@pytest.mark.asyncio
async def test_move_limited_session_sets_scheduled_at_from_parsed_reset(monkeypatch):
    """When the Notification hook path has already parsed the reset time, it's
    passed through to move_limited_session_to_resume and lands on the card's
    scheduled_at so _is_due keeps the card out of dispatch until then --
    independent of when the global dispatch_pause expires."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-scheduled", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0007"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-2", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0007",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_posts_comment_with_reset_time(monkeypatch):
    """After a successful move to To Resume, an activity comment surfaces WHY the
    card is there ("Rate-limit hit") and WHEN it will auto-resume (parsed reset
    time). Without this, the activity feed is silent and an operator has to dive
    into dispatch.py logs to understand the move."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-3", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0008"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-3", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0008",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )
    assert result is True

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("Rate-limit hit" in t for t in comment_texts)
    assert any("2026-07-11T23:10:00+02:00" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_posts_fallback_comment_when_no_reset(monkeypatch):
    """When the Notification hook path couldn't parse a reset time, the
    activity comment falls back to the same ~5h window the reaper uses -- the
    activity feed mirrors what the global dispatch pause / scheduled_at tell the
    dispatcher."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-4", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0009"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-4", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0009",
        )

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("fallback" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_no_comment_when_move_fails(monkeypatch):
    """If the resume path can't find a resumable worktree (returns False), no
    comment is posted -- the card wasn't moved and there's nothing to explain."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="no-resume", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-no-resume"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target", return_value=None,
         ):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-no-resume",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )
    assert result is False

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert not any("Rate-limit hit" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_ignores_non_worktree_cwd():
    """A cwd that isn't a `<project>/.claude/worktrees/<name>` shape (e.g. a manual
    `claude` session, or the project root itself) is left untouched."""
    result = await dispatch.move_limited_session_to_resume("/home/me/some-project")
    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_returns_false_when_no_matching_card(monkeypatch):
    """No card claimed by that session -> no-op, even if the cwd shape matches."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await _make_card(s, title="unrelated", column="engineer")
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-no-such-session",
        )

    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_returns_false_when_project_key_unresolved():
    """When the derived project path can't be resolved to a project key, bail out
    before touching the kanban DB at all."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=None
    ):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0002",
        )

    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_handles_backlog_card(monkeypatch):
    """A 429-hit session whose card already landed in Backlog (e.g. moved
    there by a prior reap that bumped dispatch_failures back to source_column)
    must still get moved to To Resume when its hook event fires — otherwise
    the card sits in Backlog with a 429-killed session, never picked up
    again. The fix: move_limited_session_to_resume accepts cards on Backlog
    and Impediment, not only agent columns."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="backlog-429", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-backlog-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-backlog", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-backlog-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-backlog"
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_handles_impediment_card(monkeypatch):
    """Same as the Backlog case, but for a card that ended up in Impediment
    before its hook event arrived — Impediment is human territory so this
    is more theoretical, but the function should be uniformly permissive."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="impediment-429", column="Impediment")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-imp-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-imp", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-imp-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.claimed_by is None


# ---- fase 2: spillover-bij-limiet (analyse §4 Optie B / §5) -----------------

@pytest.mark.asyncio
async def test_move_limited_session_spills_over_when_pool_has_capacity(monkeypatch):
    """A limit-hit card whose project has a pool with another available
    subscription is moved to To Resume WITHOUT a reset-time scheduled_at, so
    the next tick immediately re-dispatches it onto the spillover subscription
    (the just-limited provider is skipped via its per-provider pause). The
    activity comment says it's spilling over, not waiting."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    # No real usage signal in tests -> pick decision rides on paused providers.
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="spill-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-spill-0001"},
        )
        # Pool: anthropic (the card's default provider) then minimax.
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="anthropic", model=None, drempel=0.9),
            PoolEntry(provider="minimax", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-spill", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-spill-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert result is True
    assert card.column == "To Resume"
    # Spillover: scheduled_at dropped so the card is immediately dispatch-eligible.
    assert card.scheduled_at is None
    assert any("spilling over" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_pauses_when_all_subscriptions_exhausted(monkeypatch):
    """When the pool has no other available subscription (single entry, whose
    provider just hit its limit), the card falls back to the existing
    per-provider pause: To Resume + reset-time scheduled_at, waiting for reset."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="exhausted-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-exhaust-0001"},
        )
        # Single-entry pool: anthropic (the card's provider). Once it's limited
        # there is nothing to spill to.
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="anthropic", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-exhaust", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-exhaust-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert result is True
    assert card.column == "To Resume"
    # No spillover: the reset-time pause is preserved so the card waits.
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"
    assert any("Auto-resume scheduled at" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_no_pool_keeps_reset_pause(monkeypatch):
    """Backward-compat: with no subscription pool configured, the reactive
    limit path is unchanged — reset-time scheduled_at is preserved."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="nopool-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-nopool-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-nopool", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-nopool-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


@pytest.mark.asyncio
async def test_active_session_count_excludes_to_resume():
    """Cards in To Resume are excluded from _active_session_count (fixed column)."""
    async with KanbanSessionLocal() as s:
        c1 = await _make_card(s, title="active", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c1, payload={"claimed_by": "agent:k-alive-0005"},
        )
        c2 = await _make_card(s, title="resumable", column="To Resume")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c2, payload={"claimed_by": "agent:k-alive-0006"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        count = dispatch._active_session_count(cards)

    assert count == 1  # only c1 (engineer), not c2 (To Resume)


@pytest.mark.asyncio
async def test_dispatch_picks_up_to_resume_card():
    """_next_card picks unclaimed cards from To Resume when Backlog is empty."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="resume-me", column="To Resume")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.column == "To Resume"


@pytest.mark.asyncio
async def test_dispatch_prefers_to_resume_over_backlog():
    """_next_card prefers To Resume cards over Backlog cards: interrupted work is
    finished before new work is started.

    Reversed from the original Backlog-first assertion, which pinned the literal
    order of the `_DISPATCH_COLUMNS` tuple as it happened to be written in the
    initial commit rather than a decided policy. Backlog does not starve behind
    this: a limit-parked To Resume card carries a future `scheduled_at` and is
    held out of `selectable()` by `_is_due` until its reset time."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="new-task", column="Backlog")
        await _make_card(s, title="resume-me", column="To Resume")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "resume-me"
    assert next_card.column == "To Resume"


@pytest.mark.asyncio
async def test_dispatch_prefers_higher_priority_within_column():
    """_next_card picks a 'high' priority card over an older 'none'-priority card
    in the same column, even though rank order would otherwise pick the older one."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="filed-first", column="Backlog", priority=None)
        await _make_card(s, title="urgent", column="Backlog", priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "urgent"


@pytest.mark.asyncio
async def test_dispatch_orders_by_priority_high_medium_low_none():
    """_next_card ranks priority high > medium > low > none, regardless of rank order."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="none-card", column="Backlog", priority="none")
        await _make_card(s, title="low-card", column="Backlog", priority="low")
        await _make_card(s, title="medium-card", column="Backlog", priority="medium")
        await _make_card(s, title="high-card", column="Backlog", priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "high-card"


class _FakeCard:
    def __init__(self, scheduled_at=None):
        self.scheduled_at = scheduled_at


def test_is_due_none_and_empty_are_due():
    assert dispatch._is_due(_FakeCard(None)) is True
    assert dispatch._is_due(_FakeCard("")) is True


def test_is_due_malformed_value_fails_open():
    assert dispatch._is_due(_FakeCard("not-a-date")) is True


def test_is_due_naive_datetime_is_treated_as_utc():
    assert dispatch._is_due(_FakeCard("2000-01-01T00:00:00")) is True
    assert dispatch._is_due(_FakeCard("2099-01-01T00:00:00")) is False


def test_is_due_future_and_past():
    assert dispatch._is_due(_FakeCard("2099-01-01T00:00:00+00:00")) is False
    assert dispatch._is_due(_FakeCard("2000-01-01T00:00:00+00:00")) is True


@pytest.mark.asyncio
async def test_next_card_skips_future_scheduled_card():
    """A card with a future scheduled_at is invisible to auto-dispatch until due."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is None


@pytest.mark.asyncio
async def test_next_card_picks_up_due_scheduled_card():
    """Once scheduled_at is in the past, the card becomes a normal dispatch candidate."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="ready", column="Backlog",
                          scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "ready"


@pytest.mark.asyncio
async def test_auto_dispatch_tick_posts_comment_for_due_scheduled_card():
    """When the auto-dispatch tick picks up a card whose `scheduled_at` was in
    the past (i.e. auto-resuming, not force-dispatching), post an activity
    comment with the original scheduled_at so the operator can see the tick
    didn't force-dispatch early."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="auto-resume-me", column="To Resume",
                                scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("Auto-resuming" in t for t in comment_texts)
    assert any("2000-01-01T00:00:00+00:00" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_auto_dispatch_tick_no_comment_for_unscheduled_card():
    """A card without `scheduled_at` isn't 'auto-resuming' — it's just a normal
    dispatch. No auto-resume comment should be posted."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="ordinary", column="Backlog")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert not any("Auto-resuming" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_manual_dispatch_card_does_not_post_auto_resume_comment():
    """Manual `dispatch_card` is an explicit human override (UI button). It
    shouldn't post the auto-resume comment that the auto-tick path posts — the
    operator already knows they triggered this."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="manual-resume", column="To Resume",
                                scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert result is not None
    assert not any("Auto-resuming" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_next_card_prefers_unscheduled_over_future_scheduled():
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await _make_card(s, title="now", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "now"


@pytest.mark.asyncio
async def test_dispatch_all_pending_skips_future_scheduled_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await _make_card(s, title="now", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 1
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_column_preference_beats_priority():
    """The column preference is about finishing interrupted work, not urgency, so
    priority never crosses a column boundary: To Resume still wins over a 'high'
    priority Backlog card.

    The direction flipped with `_DISPATCH_COLUMNS`, but the contract this test was
    written for (commit e0acb7d3, "honor card priority in auto-dispatch ordering")
    is unchanged and is the point of the assertion: priority sorts *within* a
    column only."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="new-task", column="Backlog", priority="high")
        await _make_card(s, title="resume-me", column="To Resume", priority=None)
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "resume-me"


# ---- git-ship / session-end workflow --------------------------------------


class TestBuildShipInstructions:
    """_build_ship_instructions produces correct instructions per ship mode."""

    def test_direct_mode_includes_merge_commands(self):
        instructions = dispatch._build_ship_instructions("direct")
        # Merge happens through a throwaway detached worktree, not `git checkout
        # master` (which deterministically fails in a linked worktree — see the
        # [self-improve] card that motivated this recipe).
        # The base is resolved into `$BASE` by the ahead-aware divergence
        # guard (see test_direct_mode_includes_local_master_divergence_guard):
        # local `master` when local is at-or-ahead of origin, `origin/master`
        # in the behind-only case. Basing *unconditionally* on origin/master
        # is still wrong — on a multi-session box local master routinely
        # carries other agents' not-yet-pushed commits, and that shape would
        # strand them (kanban card 5e83b6e0…).
        assert "git worktree add --detach \"$WT\" \"$BASE\"" in instructions
        assert "git worktree add --detach \"$WT\" origin/master" not in instructions
        assert "git checkout master" not in instructions
        assert "merge --no-ff" in instructions
        assert "push origin HEAD:master" in instructions
        assert "git fetch origin" in instructions
        assert "venv/bin/activate" not in instructions  # local pytest dropped, see feedback_no_local_pytest memory
        assert "pytest -q" not in instructions
        assert "attach_deliverable" in instructions
        assert 'kind="branch"' in instructions
        assert 'move_card' in instructions
        assert '"Done"' in instructions
        assert "gh pr create" not in instructions

    def test_direct_mode_includes_local_master_divergence_guard(self):
        """Basing on local `master` requires an ahead-aware divergence guard.

        Three shapes, and only one is a genuine blocker:

          - local at-or-ahead of origin (origin/master is an ancestor of
            master): base on local `master`, which may carry other agents'
            not-yet-pushed commits.
          - behind-only (master is an ancestor of origin/master, so ahead=0):
            nothing to strand and the push is a plain fast-forward, so base on
            `origin/master` and ship. Blocking here is what made the previous
            revision self-reinforcing on a busy box — the post-push
            `pull --ff-only` skips with a WARN whenever the main checkout is
            dirty, local master then falls behind, and every later ship
            tripped the guard with nothing actually at risk.
          - true divergence (both sides have unique commits): fail fast with a
            remediation, because either base would silently discard work.
        """
        instructions = dispatch._build_ship_instructions("direct")
        # The guard itself: both ancestry probes must be present.
        assert "git merge-base --is-ancestor origin/master master" in instructions
        assert "git merge-base --is-ancestor master origin/master" in instructions
        # The behind-only carve-out must survive — without pinning it, a
        # regression back to the always-block shape would go unnoticed.
        assert "BASE=master" in instructions
        assert "BASE=origin/master" in instructions
        # Error message + remediation hint: an agent staring at this needs
        # to know what to do next. The remediation interpolates the absolute
        # path of the main checkout (the legacy fallback uses the hardcoded
        # meta-project root, so the rendered form is `git -C
        # /home/vdvgu/claude-cockpit pull --rebase origin master`). The
        # bash continuation in the source spans two physical lines, so check
        # the joined substring across the line break.
        assert "ERROR: local master has DIVERGED from origin/master" in instructions
        joined = " ".join(line.strip() for line in instructions.splitlines())
        assert "pull --rebase origin master" in joined
        # The legendary two-labels-swap regression — pin the labels as
        # ordered so the BEHIND/AHEAD log can't silently flip back.
        assert "ahead=" in instructions and "behind=" in instructions
        assert "BEHIND=$(git rev-list --count master..origin/master" in instructions
        assert "AHEAD=$(git rev-list --count origin/master..master" in instructions

    def test_direct_mode_includes_post_push_main_checkout_sync(self):
        """The post-push main-checkout sync must fast-forward the canonical
        checkout where `master` is actually checked out.

        The throwaway worktree is detached HEAD — it cannot update `master`
        itself. Without a sync of the main checkout, every subsequent ship
        on this multi-session box trips the divergence guard above (the
        ship pushed, but the local master ref didn't move), even though the
        divergence is fully explained by our own push. The sync must:

        1. Run ONLY after a successful push (the next guarded `git push`).
        2. Try `git -C "$MAIN_CHECKOUT" pull --ff-only origin master` to
           fast-forward the local master ref AND update the working tree.
        3. Skip-with-WARN when the pull refuses (dirty working tree from a
           concurrent agent's edit, or non-fast-forward) — the chosen
           trade-off (kanban card 5e83b6e0… round 3): the push already
           landed on origin, only the next ship will see the divergence.
        4. NOT fall back to `git update-ref refs/heads/master origin/master`.
           The earlier implementation tried that as a fallback, but
           `update-ref` almost always succeeds — which silenced the WARN
           the human decision explicitly required. With the trade-off
           accepted (the divergence guard will trip on the next ship
           until the operator reconciles), the visible signal is more
           valuable than a hidden ref-update.
        """
        instructions = dispatch._build_ship_instructions("direct")
        # (1) the sync is inside the `if git -C "$WT" push origin HEAD:master`
        # branch — place-anchored by looking at the slice after the push.
        push_idx = instructions.index("push origin HEAD:master")
        post_push = instructions[push_idx:]
        # (2) fast-forward pull against the main checkout.
        assert "pull --ff-only origin master" in post_push
        # (3) WARN — the human-recognisable signal that the sync was skipped.
        # Specifically tied to the pull failure (the WARN is inside the
        # `if ! git -C "$MAIN_CHECKOUT" pull --ff-only origin master`
        # branch), not just a stray "WARN" anywhere in the post-push
        # block. Pins the "skip-with-WARN" semantics — a future editor
        # who accidentally demotes the WARN to prose inside a comment
        # (the `efb8187b…` / `c06a3a2a…` failure shape) is caught here.
        pull_branch_idx = post_push.index(
            'git -C "$MAIN_CHECKOUT" pull --ff-only origin master'
        )
        # The first line after the pull command's `if` opener must be the
        # WARN's `echo` — that's the shape that ties the WARN to the pull
        # failure (the WARN sits inside the `if ! git … pull --ff-only`,
        # not in some unrelated branch). A future editor who demotes the
        # WARN to prose-after-an-exit (the `efb8187b…` shape) trips here.
        first_line_after_pull = post_push[pull_branch_idx:].split("\n", 1)[0]
        assert "pull --ff-only" in first_line_after_pull, (
            "sanity: post-push sync must START with the pull --ff-only "
            "command. Got: " + repr(first_line_after_pull)
        )
        # Slice everything after the pull opener (the `if ! git …` line is
        # itself multi-line; the WARN lives inside the `if`/`fi` block).
        # Walk to the next `fi` at the same indentation level to land on
        # the closing brace of the pull's failure branch.
        post_pull_block = post_push[pull_branch_idx:]
        # The WARN echo must live inside the pull's failure branch —
        # anywhere between the `if ! git … pull` and the matching `fi`.
        block_idx = post_pull_block.index("pull --ff-only origin master")
        warn_after_pull = post_pull_block[block_idx:]
        assert "WARN" in warn_after_pull, (
            "post-push sync must emit a visible WARN when the pull fails "
            "(kanban card 5e83b6e0… round 3 blocker 1). The WARN must be "
            "tied to the pull failure, not just present anywhere in the "
            "post-push block."
        )
        # (4) the broken `update-ref` fallback must not appear. The previous
        # implementation inlined it; it succeeded silently and bypassed the
        # WARN, which is the exact opposite of the human's decision.
        assert "update-ref refs/heads/master origin/master" not in post_push, (
            "post-push sync must NOT fall back to `update-ref` — the "
            "fallback silently bypassed the WARN the human decision "
            "explicitly required (kanban card 5e83b6e0… round 3 blocker 1). "
            "The trade-off (divergence guard trips on next ship until the "
            "operator reconciles) is explicitly accepted."
        )

    def test_direct_mode_main_checkout_path_is_properly_shlex_quoted(self):
        """The ``MAIN_CHECKOUT`` interpolation must use ``shlex.quote``'s output
        bare — no surrounding double quotes (kanban card 5e83b6e0… blocker).

        The pre-quoted form already wraps the path in single quotes (only
        when the path actually contains shell metacharacters — for a clean
        path like ``/home/vdvgu/claude-cockpit`` with no spaces, ``$``,
        backtick, or embedded double-quote, ``shlex.quote`` returns the
        path bare, no quotes needed). Wrapping the pre-quoted value in
        literal double quotes
        inside the bash interpolation produces
        ``MAIN_CHECKOUT="'/path with spaces'"`` for a path WITH spaces —
        the shell sees literal single quotes inside the double-quoted
        assignment, and ``$MAIN_CHECKOUT`` expands to ``'/path with
        spaces'`` (with the single quotes still attached), breaking every
        downstream ``git -C "$MAIN_CHECKOUT" …`` with
        ``fatal: cannot change to ``'/path with spaces'``: No such file or
        directory``. The fail-open ``2>/dev/null`` on the post-push sync
        meant the bug was silent — the push landed, the ref-update fired,
        but the main checkout stayed on the old tree (the 74-staged-deletions
        shape, kanban card 5e83b6e0… round 3).

        Pin the safe form for the legacy fallback path (no metacharacters,
        so shlex.quote returns the path bare) and the broken form as
        absent. The path-with-metacharacters case is covered by
        ``test_quote_safety_path_with_spaces`` /
        ``test_quote_safety_path_with_dollar`` in
        ``test_kanban_dispatch.py`` — the same shlex.quote contract powers
        both checks.
        """
        instructions = dispatch._build_ship_instructions("direct")
        # Safe form: `MAIN_CHECKOUT=` followed by the path (bare, no quotes
        # because the legacy fallback path has no metacharacters). The
        # broken form would show `MAIN_CHECKOUT="/home/vdvgu/claude-cockpit"`
        # (double-quoted) — assert that shape is absent.
        assert "MAIN_CHECKOUT=/home/vdvgu/claude-cockpit" in instructions, (
            "expected the safe rendered form `MAIN_CHECKOUT=/home/vdvgu/claude-cockpit` "
            "(shlex.quote output used bare, no surrounding double quotes). "
            "The current form is the broken double-quoted variant from "
            "kanban card 5e83b6e0… round 3."
        )
        bad_double_quoted = 'MAIN_CHECKOUT="/home/vdvgu/claude-cockpit"'
        assert bad_double_quoted not in instructions, (
            "MAIN_CHECKOUT is rendered with literal double quotes around "
            "the path — this is the blocker from kanban card 5e83b6e0… "
            "round 3. shlex.quote already wraps the path in single quotes "
            "when needed; wrapping it again in double quotes makes "
            "`$MAIN_CHECKOUT` expand to the literal string `'<path>'` (with "
            "attached quotes). Use the pre-quoted form bare: "
            "`MAIN_CHECKOUT={main_checkout_q}`."
        )

    def test_direct_mode_post_push_sync_uses_main_checkout_var(self):
        """The post-push sync must target ``$MAIN_CHECKOUT``, not hardcode a
        concrete path. The dispatch prompt inlines ``project_path`` via
        ``shlex.quote`` so the path is portable across meta + product
        projects (kaart a962b209… blocker C). A hardcoded
        ``/home/vdvgu/claude-cockpit`` would silently break every non-meta
        project — the same class of bug as the original double-quoting bug,
        just a different surface.
        """
        instructions = dispatch._build_ship_instructions("direct")
        # The sync must reference the variable, not the literal path.
        push_idx = instructions.index("push origin HEAD:master")
        post_push = instructions[push_idx:]
        assert "git -C \"$MAIN_CHECKOUT\"" in post_push
        # And the legacy escape (echoing the path in prose) must not also
        # appear, so a future editor can't have both forms.
        assert "git -C \"/home/vdvgu/claude-cockpit\"" not in post_push

    def test_pull_request_mode_includes_gh_commands(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "gh pr create --draft" in instructions
        assert "git push -u origin HEAD" in instructions
        assert "git fetch origin" in instructions
        assert "venv/bin/activate" not in instructions  # local pytest dropped, see feedback_no_local_pytest memory
        assert "pytest -q" not in instructions
        assert "attach_deliverable" in instructions
        assert 'kind="pr"' in instructions
        assert 'move_card' in instructions
        assert '"Done"' in instructions
        assert "merge --no-ff" not in instructions
        assert "git worktree add --detach" not in instructions

    def test_both_modes_instruct_running_tests_before_shipping(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "no pre-push gate" in instructions
            assert "venv/bin/activate" not in instructions  # backend gate is quality.yml CI only, not local
            assert "pytest -q" not in instructions
            assert "npm run lint" in instructions
            assert "npm run build" in instructions
            assert "quality.yml" in instructions  # backend gate, mentioned as such
            assert "Never ship" in instructions
            assert "commit your work" in instructions.lower() or "Commit your work" in instructions

    def test_both_modes_include_sync_step(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "git fetch origin" in instructions

    def test_frontend_gate_is_conditional_on_frontend_diff(self):
        """The frontend lint+build gate must only run when the branch actually
        touches ``frontend/`` — a docs-/backend-only branch would otherwise pay
        a multi-minute ``npm ci`` + build for zero coverage. The instructions
        must (a) probe the branch diff scoped to ``frontend/``, (b) keep the
        lint+build command for the touched case, and (c) emit a visible skip
        log for the untouched case."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # (a) diff probe scoped to frontend/ against the branch base
            # (merge-base variant — origin/master alone false-positives when
            # master advanced on frontend/ since the branch was cut; card cd7ff20b)
            assert "git merge-base HEAD origin/master" in instructions
            assert "git diff --name-only \"$BASE\"" in instructions
            assert "git diff --name-only origin/master -- frontend/" not in instructions
            # untracked frontend files count too (fresh files not yet committed)
            assert "git ls-files --others --exclude-standard -- frontend/" in instructions
            # (b) the actual gate command survives, guarded by the probe
            assert "npm run lint && npm run build" in instructions
            # (c) explicit skip log when there is no frontend diff
            assert "geen frontend-diff — gate overgeslagen" in instructions

    def test_frontend_gate_installs_deps_when_node_modules_missing(self):
        """A dispatched worktree is a fresh ``git worktree add`` off
        origin/master with no ``node_modules`` (gitignored), so the frontend
        gate must install deps before running lint/build — otherwise the first
        run dies with ``eslint: not found`` / ``vite: not found``. The install
        must be guarded on a missing ``node_modules`` so repeat runs within the
        same session don't re-pay the ~40s install."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # reproducible install matching CI (quality.yml uses `npm ci`)
            assert "npm ci" in instructions
            # only install when node_modules is absent
            assert "-d node_modules" in instructions or "-d frontend/node_modules" in instructions

    def test_frontend_gate_symlinks_main_node_modules_when_lockfile_matches(self):
        """Symptom (card 15cc257d…): a fresh worktree's `npm ci` adds ~40-90s
        to every frontend-touching card. When ``frontend/package-lock.json``
        is identical to origin/master, the main checkout's already-installed
        ``frontend/node_modules`` is safe to symlink — the lockfile diff
        against master is the correctness gate. The frontend gate must use
        this fast path; only fall back to ``npm ci`` when the lockfile
        diverged (a frontend-deps change)."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # the shortcut itself: a symlink of the main checkout's node_modules
            assert "ln -s" in instructions
            # the lockfile-diff gate that decides whether the shortcut is safe
            assert "package-lock.json" in instructions
            # uses the same merge-base used by the FRONTEND_TOUCHED probe, so
            # the comparison never lies when master advanced since the branch
            # was cut (regression: card cd7ff20b)
            assert "git merge-base HEAD origin/master" in instructions
            # the fallback path remains documented — npm ci is the recovery
            # when the lockfile diverges OR main's node_modules is absent
            assert "npm ci" in instructions

    def test_frontend_gate_moves_partial_node_modules_aside_before_symlinking(self):
        """Secondary papercut (card 15cc257d…): an interrupted ``npm ci``
        leaves a partial ``node_modules`` (some scoped dirs present but
        missing ``.bin/``), which then fails confusingly with
        ``eslint: not found`` and blocks a plain symlink until moved aside.
        ``rm`` is deny-listed in ``.claude/settings.json``, so cleanup must
        use ``mv`` and the gate must detect the partial state by the missing
        ``.bin/`` directory."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # detect partial install: node_modules exists but .bin/ does not
            assert "node_modules/.bin" in instructions
            # move it aside — `rm` is deny-listed, `mv` is the only safe move
            assert " mv " in instructions or instructions.startswith("mv ")
            # must NOT suggest `rm -rf node_modules` (rm is deny-listed)
            assert "rm -rf" not in instructions
            assert "rm -fr" not in instructions
            assert "rm node_modules" not in instructions

    def test_frontend_gate_probes_symlinked_install_for_deeper_corruption(self):
        """Tertiary papercut (card 9b7c2a98…, revisit of 4279448c): the
        fast-path symlink shortcut only checks that ``.bin/`` exists,
        which catches the partial-install trap (card 15cc257d…) but
        misses a subtler form of corruption where ``.bin/eslint`` is
        present but a *deeper* transitive dependency (e.g. ``acorn``,
        imported by ``espree``/``eslint``) is missing. The symlinked
        install then dies with ``Cannot find module 'acorn'`` on the
        first ``npm run lint``, forcing the agent to manually
        ``rm`` (deny-listed, so ``mv``) the symlink and re-pay the
        multi-minute ``npm ci``. The gate must probe the symlinked
        install for actual workability — not just for the presence of
        ``.bin/`` — and fall back to ``npm ci`` on probe failure.

        ESLint 10 (card 1e6fbe4e…) removed ``lib/cli.js`` as a resolvable
        subpath, so the legacy ``require('eslint/lib/cli.js')`` probe
        is a false negative on healthy installs; the recipe now uses
        ``require('eslint')`` (the package's main entry, resolved via
        its ``exports`` field) plus ``require.resolve('espree')`` and
        ``require.resolve('acorn')`` to cover the deeper-dep class."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # The probe must actually exercise eslint's module graph
            # (not just `require.resolve`, which only checks file
            # presence). The current shape loads the eslint main module
            # and resolves the two critical transitive deps the lint
            # pipeline actually uses; older valid shapes (the pre-ESLint-10
            # `lib/cli.js` probe and the `.bin/eslint --version` exec)
            # remain accepted to avoid silent regression during the
            # upgrade window. A pure `[ -d .bin ]` check is insufficient —
            # that's exactly what the card asks us to *replace*.
            assert (
                "require('eslint'); require.resolve('espree'); require.resolve('acorn')" in instructions
                or "require('eslint/lib/cli.js')" in instructions
                or "node_modules/.bin/eslint --version" in instructions
            ), (
                f"{mode} ship recipe must probe the symlinked install "
                "for deeper-dep corruption, not just .bin presence"
            )
            # The probe must drive a real fallback to `npm ci` when it
            # fails — `npm ci` is the canonical recovery (pinned by
            # `test_frontend_gate_installs_deps_when_node_modules_missing`).
            # We don't assert the literal token here because the fallback
            # block already lives in the existing else-branch; instead
            # we assert the probe runs *after* the symlink so the
            # fallback path is reachable from a corrupt-symlink state.
            assert "ln -s" in instructions
            # The corrupt symlink must be moved aside (`rm` is
            # deny-listed, `mv` is the only safe move), matching the
            # partial-install convention above.
            assert " mv " in instructions, \
                f"{mode} ship recipe must `mv` a corrupt symlink aside"

    def test_pull_request_mode_polls_for_merge_before_done(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "gh pr ready" in instructions
        assert "gh pr merge --auto --squash" in instructions
        assert "mergeStateStatus" in instructions
        assert "report_impediment" in instructions
        # Regression guard: BLOCKED (and other mergeStateStatus values) can mean
        # "checks still pending", not "checks failed" — a naive case-match that
        # treats *BLOCKED* as failure would false-fail on every PR the instant
        # CI starts running. Failure detection must instead be based on actual
        # check conclusions.
        assert "*BLOCKED*|CLOSED*) echo" not in instructions
        assert "FAILED" in instructions
        assert "statusCheckRollup" in instructions
        # A wedged PR must not poll forever.
        assert "ITER" in instructions and "40" in instructions

    def test_both_modes_require_a_summary_when_moving_to_done(self):
        """move_card requires `summary` on Done/Impediment (see mcp_server.py);
        the instructions must tell the agent to actually pass it, otherwise every
        move_card("Done") call in the wild fails on summary_required."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "summary=" in instructions
            assert "summary_required" not in instructions  # not the agent's problem to debug


class TestBuildShipInstructionsSessionRetro:
    """The session-end retro step is injected between attach_deliverable and
    move_card→Done for executor/engineer cards in both ship modes (the analyst
    path is wired separately). The retro is the engine behind
    self-improvement: a `[self-improve]` card filed here survives past the
    transcript and lands on the dispatcher queue, while a comment in the
    transcript does not.
    """

    def test_direct_mode_includes_session_retro_step(self):
        instructions = dispatch._build_ship_instructions("direct")
        assert "session-retro" in instructions
        assert "self-improve" in instructions
        assert ".claude/skills/session-retro/SKILL.md" in instructions

    def test_pull_request_mode_includes_session_retro_step(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "session-retro" in instructions
        assert "self-improve" in instructions
        assert ".claude/skills/session-retro/SKILL.md" in instructions

    def test_session_retro_step_runs_after_attach_deliverable_and_before_move_card(self):
        """Acceptance: the retro must be the *last* step before move_card→Done
        (after ship + attach_deliverable, never before them). A retro wired
        earlier would burn time on lessons that ship-discipline should catch.

        Anchor on numbered step headings (``N. **...**``) rather than bare
        tool names. The bare ``move_card`` token legitimately appears
        elsewhere in the prompt (pre-ship blocks, post-mortems, any block
        that mentions the tool in passing), so an ``index("move_card")``
        match on a pre-ship mention would shadow the canonical step and
        either false-positive or false-negative the ordering check (incident
        reproduced during the FCR step wiring of `_build_ship_instructions`
        — see [self-improve] card fff81b84…).
        """
        # Anchors per mode — the step numbers differ between direct (5/6/7)
        # and pull-request (6/7/8) per the shared numbering contract verified
        # by ``test_session_retro_step_uses_consistent_step_numbering``.
        step_anchors = {
            "direct": (
                "5. **Attach the deliverable**",
                "6. **Run the session-end retro**",
                "7. **Move the card to Done**",
            ),
            "pull-request": (
                "6. **Attach the deliverable**",
                "7. **Run the session-end retro**",
                "8. **Move the card**",
            ),
        }
        for mode, (attach_h, retro_h, move_h) in step_anchors.items():
            instructions = dispatch._build_ship_instructions(mode)
            # Each anchor must occur exactly once — a bare ``index()`` against
            # a non-unique anchor would silently mask ordering bugs.
            assert instructions.count(attach_h) == 1, (
                f"attach anchor not unique in {mode}: "
                f"{instructions.count(attach_h)} matches of {attach_h!r}"
            )
            assert instructions.count(retro_h) == 1, (
                f"retro anchor not unique in {mode}: "
                f"{instructions.count(retro_h)} matches of {retro_h!r}"
            )
            assert instructions.count(move_h) == 1, (
                f"move anchor not unique in {mode}: "
                f"{instructions.count(move_h)} matches of {move_h!r}"
            )
            attach_idx = instructions.index(attach_h)
            retro_idx = instructions.index(retro_h)
            move_idx = instructions.index(move_h)
            assert attach_idx < retro_idx < move_idx, (
                f"order broken in {mode}: "
                f"attach@{attach_idx} retro@{retro_idx} move@{move_idx}"
            )

    def test_session_retro_step_uses_consistent_step_numbering(self):
        """The retro step is renumbered in each mode to fit between attach (5/6)
        and move (7/8). Regression guard: if the step number drifts, the agent
        loses its place."""
        # direct: attach=5, retro=6, move=7
        direct = dispatch._build_ship_instructions("direct")
        assert "5. **Attach the deliverable**" in direct
        assert "6. **Run the session-end retro**" in direct
        assert "7. **Move the card to Done**" in direct
        # pull-request: attach=6, retro=7, move=8
        pr = dispatch._build_ship_instructions("pull-request")
        assert "6. **Attach the deliverable**" in pr
        assert "7. **Run the session-end retro**" in pr
        assert "8. **Move the card**" in pr

    def test_build_card_prompt_includes_session_retro_step(self):
        """The retro step reaches the dispatch prompt (not just the helper)."""
        class _C:
            title = "My Card"
            description = "Do the thing"
        for mode in ("direct", "pull-request"):
            prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode=mode)
            assert "session-retro" in prompt, f"missing in {mode} prompt"
            assert "self-improve" in prompt, f"missing in {mode} prompt"


class TestBuildCardPromptSessionEnd:
    """build_card_prompt includes the Session-end workflow section."""

    def test_direct_mode_includes_session_end_section(self):
        class _C:
            title = "My Card"
            description = "Do the thing"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Session-end workflow" in prompt
        assert "merge --no-ff" in prompt
        assert "push origin HEAD:master" in prompt
        assert "move_card" in prompt
        assert '"Done"' in prompt

    def test_pull_request_mode_includes_session_end_section(self):
        class _C:
            title = "My Card"
            description = "Do the thing"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="pull-request")
        assert "Session-end workflow" in prompt
        assert "gh pr create --draft" in prompt
        assert "git push -u origin HEAD" in prompt
        assert "move_card" in prompt
        assert '"Done"' in prompt

    def test_impediment_card_still_has_session_end_section(self):
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Where is the crash?",
        )
        assert "IMPEDIMENT" in prompt
        assert "Session-end workflow" in prompt
        assert "merge --no-ff" in prompt

    def test_session_end_section_comes_after_main_instructions(self):
        class _C:
            title = "T"
            description = ""
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        # The session-end section should appear after the main "Work autonomously" paragraph
        main_idx = prompt.index("Work autonomously")
        ship_idx = prompt.index("Session-end workflow")
        assert ship_idx > main_idx, "Session-end workflow should appear after main instructions"

    def test_impediment_prompt_renders_answer_as_authoritative(self):
        """When resolve_impediment forwards a chosen gate answer as the separate
        `impediment_answer` field, build_card_prompt must surface it under the
        `## IMPEDIMENT` section as an authoritative decision — so the resumed
        agent acts on it instead of re-asking the question."""
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Postgres or SQLite?",
            impediment_answer="Postgres",
        )
        assert "## IMPEDIMENT" in prompt
        assert "Postgres or SQLite?" in prompt
        # The chosen answer is rendered as authoritative (decision language),
        # not as an open question — so the resumed session acts on it.
        assert "Postgres" in prompt
        assert "authoritative" in prompt

    def test_impediment_prompt_without_answer_keeps_legacy_question_framing(self):
        """Backwards compat: when no answer was given (legacy free-text
        impediment), the IMPEDIMENT section keeps the open-question framing
        instead of the authoritative-decision framing."""
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Where is the crash?",
        )
        assert "## IMPEDIMENT" in prompt
        assert "Where is the crash?" in prompt
        assert "clarify what's needed" in prompt


class TestBuildCardPromptHostCardId:
    """The dispatched agent must see its host card's full id in the prompt
    header, so it can call `comment`/`attach_deliverable`/`move_card` on the
    right card by id instead of guessing from the prose (which may quote other
    card ids, leading to short-prefix collisions — see kanban card "Executor
    prompt omits host card_id; ids in card text mislead MCP calls")."""

    def test_executor_prompt_includes_host_card_id_label(self):
        class _C:
            title = "T"
            description = ""
            id = "abcdef1234567890"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Host card id: abcdef1234567890" in prompt

    def test_analyst_prompt_includes_host_card_id_label(self):
        """Analyst phase renders a lighter ship-instructions block, but the
        host-card-id line lives above the phase split and must surface in
        both phases."""
        class _C:
            title = "T"
            description = ""
            id = "abcdef1234567890"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                            phase="analyst")
        assert "Host card id: abcdef1234567890" in prompt

    def test_host_card_id_appears_unambiguously_when_description_quotes_other_ids(self):
        """Regression for the actual bug: the card description cites another
        card's short id (`3ffdc75e`), and the agent mistook it for the host
        id. With an explicit `Host card id:` label, the agent copies the
        labeled value verbatim instead of scraping ids from prose."""
        class _C:
            title = "Self-improve card"
            description = (
                "Earlier evidence mentioned card 3ffdc75e but that's a "
                "different Done card. This card's id is the real one."
            )
            id = "5b63cafe00000001"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Host card id: 5b63cafe00000001" in prompt
        # The misleading short id still appears in the description (that's
        # fine — it's evidence text), but the host id is unambiguous.
        assert "Host card id: 3ffdc75e" not in prompt


class TestBuildCardPromptSpecDoc:
    """``card.meta['spec_doc']`` (the analyst-set *implements*-link to a
    ``docs/cockpit/*.md`` design/analysis doc) must reach the dispatch prompt
    verbatim — otherwise the executor's ship-stap 3 ("voeg een `✅
    Geïmplementeerd (kaart <id>)`-regel toe aan het brondoc") is blind to
    which doc to update, and the bijwerk-stap gets skipped or lands on the
    wrong file (kanban card 87ced87b…)."""

    def test_prompt_renders_brondoc_line_when_spec_doc_present(self):
        class _C:
            title = "T"
            description = ""
            meta = {"spec_doc": "docs/cockpit/some-decision.md"}
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "**Brondoc (spec_doc):** `docs/cockpit/some-decision.md`" in prompt

    def test_prompt_omits_brondoc_line_when_spec_doc_absent(self):
        """Legacy cards (and analysis/feature cards without an analyst-set
        spec_doc) must render unchanged — no rendered `**Brondoc (spec_doc):**
        …` line in the kaart-context block. The ship-instructions paragraph
        still MENTIONS the spec_doc marker (it documents the step), so we
        assert against the rendered line shape, not the bare word."""
        class _C:
            title = "T"
            description = ""
            meta = None
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        # No rendered brondoc-line between the title/description and the
        # `Ship mode:` marker (the kaart-context block).
        import re
        ship_mode_idx = prompt.index("Ship mode:")
        kaart_block = prompt[:ship_mode_idx]
        assert not re.search(r"^\*\*Brondoc \(spec_doc\):\*\*", kaart_block, re.MULTILINE)

    def test_prompt_omits_brondoc_line_when_meta_empty(self):
        """A card whose meta exists but carries no spec_doc key is also
        'no brondoc' — render unchanged."""
        class _C:
            title = "T"
            description = ""
            meta = {"labels": ["x"]}
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        import re
        ship_mode_idx = prompt.index("Ship mode:")
        kaart_block = prompt[:ship_mode_idx]
        assert not re.search(r"^\*\*Brondoc \(spec_doc\):\*\*", kaart_block, re.MULTILINE)

    def test_prompt_tolerates_non_string_spec_doc(self):
        """Garbage in `meta['spec_doc']` (int / None / list) must NOT crash
        the renderer — fall back to no-line rather than 500'ing dispatch.
        Analysts are supposed to write a string, but the helper is defensive."""
        class _C:
            title = "T"
            description = ""
            meta = {"spec_doc": 12345}
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        import re
        ship_mode_idx = prompt.index("Ship mode:")
        kaart_block = prompt[:ship_mode_idx]
        assert not re.search(r"^\*\*Brondoc \(spec_doc\):\*\*", kaart_block, re.MULTILINE)

    def test_brondoc_line_appears_above_screenshots_section(self):
        """The spec_doc line must land in the kaart-context block (between
        description and `Ship mode:`) so the executor sees it BEFORE the
        ship-instructions paragraph that references it. Anchored against the
        existing `## Screenshots` heading so a refactor that splits the
        build into more pieces still keeps the ordering."""
        class _C:
            title = "T"
            description = "do thing"
            meta = {"spec_doc": "docs/cockpit/x.md"}
            attachments = []
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        brondoc_idx = prompt.index("**Brondoc (spec_doc):**")
        # Ship-instructions always mentions the marker; the *rendered* line
        # must precede the work-autonomously paragraph.
        work_idx = prompt.index("Work autonomously")
        assert brondoc_idx < work_idx, (
            "brondoc line must appear above the ship-instructions block"
        )


# ---- run_dispatch_tick honours the global usage-limit pause ----------------

@pytest.mark.asyncio
async def test_run_dispatch_tick_skips_everything_when_paused(monkeypatch):
    """When a global dispatch pause is active (Claude usage limit hit), the tick
    must not touch queued-card retries or per-project dispatch at all --
    respawning while the account-wide limit is still active would just bounce
    the card straight back to "To Resume" and re-trigger the same limit."""
    import unittest.mock as mock
    from datetime import datetime, timedelta

    import app.kanban.db as kdb
    from app.kanban.dispatch_pause import set_paused_until

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=5))
        await s.commit()

    with mock.patch.object(dispatch, "_retry_queued_cards") as retry_mock, \
         mock.patch.object(dispatch, "list_autodispatch_projects") as list_mock:
        await dispatch.run_dispatch_tick()

    retry_mock.assert_not_called()
    list_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_dispatch_tick_runs_normally_when_not_paused(monkeypatch):
    """Sanity check: the new pause guard must not block a tick when there is no
    active pause -- otherwise every project's auto-dispatch would silently die."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    with mock.patch.object(dispatch, "_retry_queued_cards") as retry_mock, \
         mock.patch.object(dispatch, "list_autodispatch_projects", return_value=[]) as list_mock:
        await dispatch.run_dispatch_tick()

    retry_mock.assert_called_once()
    list_mock.assert_called_once()


# ---- clear_dispatch_pause (manual operator override) ----------------------

@pytest.mark.asyncio
async def test_clear_dispatch_pause_clears_an_active_pause():
    from datetime import datetime, timedelta

    from app.kanban.dispatch_pause import is_dispatch_paused, set_paused_until

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        await s.commit()
        assert (cleared, was_paused) == (True, True)

    async with KanbanSessionLocal() as s:
        assert await is_dispatch_paused(s) is False


@pytest.mark.asyncio
async def test_clear_dispatch_pause_is_noop_when_not_paused():
    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        assert (cleared, was_paused) == (False, False)


@pytest.mark.asyncio
async def test_clear_dispatch_pause_comments_on_to_resume_cards():
    from datetime import datetime, timedelta

    from app.kanban.dispatch_pause import set_paused_until

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Rate limited", column="To Resume")
        other_cid = await _make_card(s, title="Untouched", column="Backlog")
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        await s.commit()
        assert (cleared, was_paused) == (True, True)

    async with KanbanSessionLocal() as s:
        to_resume_activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in to_resume_activity if op.op_type == "comment"
        ]
        assert any("cleared manually" in text for text in comment_texts)

        other_activity = await service.card_activity(s, other_cid)
        assert not any(op.op_type == "comment" for op in other_activity)


@pytest.mark.asyncio
async def test_clear_dispatch_pause_lets_next_tick_run(monkeypatch):
    """After a manual clear, the next dispatch tick must not be skipped -- this
    is the actual point of the override: unstick a tick the auto-detection
    paused incorrectly, without waiting for the wall-clock deadline."""
    import unittest.mock as mock
    from datetime import datetime, timedelta

    import app.kanban.db as kdb
    from app.kanban.dispatch_pause import set_paused_until

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.clear_dispatch_pause(s)
        await s.commit()

    with mock.patch.object(dispatch, "_retry_queued_cards") as retry_mock, \
         mock.patch.object(dispatch, "list_autodispatch_projects", return_value=[]) as list_mock:
        await dispatch.run_dispatch_tick()

    retry_mock.assert_called_once()
    list_mock.assert_called_once()


# ---- dead-on-arrival circuit breaker (dispatch_failures -> Impediment) ----
#
# A session that dies within seconds of being claimed (stale --resume worktree,
# missing sandcastle config, ...) used to loop forever: claimed, reaped as dead,
# re-claimed by the very next tick, dead again. reap_stale_claims now counts
# consecutive dead-on-arrival reaps per card and moves it to Impediment after
# MAX_DISPATCH_FAILURES instead of retrying forever.

async def _backdate_claim(s, card_id: str, seconds_ago: float) -> None:
    """Rewrite a card's claimed_at directly (bypassing the op-log) to simulate a
    session that ran for a while before dying, rather than dying on arrival."""
    from datetime import datetime, timedelta

    card = await s.get(KanbanCard, card_id)
    card.claimed_at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    await s.flush()


@pytest.mark.asyncio
async def test_reap_increments_dispatch_failures_on_dead_on_arrival():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="doa", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-doa-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert reaped == 1
    assert card.claimed_by is None
    assert card.dispatch_failures == 1
    assert card.column == "engineer"  # still under MAX_DISPATCH_FAILURES, not moved


@pytest.mark.asyncio
async def test_reap_moves_to_impediment_after_max_dispatch_failures():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="doa", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-doa-0002"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0  # reset so a future redispatch starts fresh
    # tagged so the board renders it red — a technical dispatch failure, not a
    # human-parked impediment (see dispatch.ERROR_LABEL / CardItem.tsx)
    assert dispatch.ERROR_LABEL in (card.labels or [])


@pytest.mark.asyncio
async def test_reap_clears_stale_resume_fields_on_dead_on_arrival():
    # A resume_session_id/resume_project_folder pointing at a worktree that was
    # since merged and GC'd would otherwise be retried forever by
    # get_transport_for_card, dying in seconds every time.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-session",
                                     "resume_project_folder": "-old-worktree"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stale-0001"},
        )
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1


@pytest.mark.asyncio
async def test_reap_does_not_count_failure_for_long_running_claim():
    # A session that ran for a while before dying (real crash, OOM, manual kill)
    # proved the dispatch target itself works -- must not count toward the
    # dead-on-arrival circuit breaker.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="ran-a-while", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-ran-0001"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS + 10)
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert reaped == 1
    assert card.claimed_by is None
    assert card.dispatch_failures == 0
    assert card.column == "engineer"


@pytest.mark.asyncio
async def test_repeated_synchronous_spawn_failures_move_to_impediment():
    # A synchronous spawn exception (e.g. resolve_directory raising because a
    # --resume worktree was merged and GC'd -- the "voorbereiding public repo"
    # case) is a different code path from the tmux dead-session reaper, but must
    # trip the same MAX_DISPATCH_FAILURES circuit breaker instead of looping
    # forever between source_column and a fresh claim.
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="always-fails", column="To Resume")
        await s.commit()

    # dispatch_project no longer propagates a synchronous spawn failure --
    # it applies the compensating ops (release, bump dispatch_failures,
    # move back / to Impediment) itself, same as before, but returns
    # normally instead of raising (kaart 05592c13…).
    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0
    assert len(transport.calls) == dispatch.MAX_DISPATCH_FAILURES


@pytest.mark.asyncio
async def test_awaitable_spawn_failure_uses_dispatch_failure_path():
    class AsyncFailingTransport(RecordingTransport):
        def __call__(self, **kwargs):
            self.calls.append(kwargs)

            async def fail():
                raise RuntimeError(
                    "Claude Code skipped MCP server configuration: "
                    "cockpit-kanban (invalid_config): expected string"
                )

            return fail()

    transport = AsyncFailingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="async-mcp-fails", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert comments
    assert "cockpit-kanban" in comments[-1]


@pytest.mark.asyncio
async def test_synchronous_spawn_failure_comment_includes_last_error():
    """When a synchronous spawn exception (str(exc)) trips
    MAX_DISPATCH_FAILURES, the auto-move comment must include the actual
    error message — not just the generic "Check the backend logs" hint —
    so triage doesn't need a logs-dive. Verifies kanban card
    5ec5a68013da4422b0a49fb2731cb8a7 ("Impediment-comment toont echte
    spawn-error niet")."""
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="explode-with-error", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, card.id)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # RecordingTransport raises `RuntimeError("tmux exploded")` so str(exc)
    # is "tmux exploded" — the comment must carry it (not just the legacy
    # "check the logs" hint) for the operator to triage in one read.
    assert "tmux exploded" in failure_comments[-1]
    # The structured prefix must remain intact — `impediment_status_for_card`
    # uses it to classify the card as dispatch_failed (not needs_answer).
    assert failure_comments[-1].startswith("[dispatch-failure]")


@pytest.mark.asyncio
async def test_synchronous_spawn_failure_comment_truncates_long_error(monkeypatch):
    """A pathological exception (10 KB of noise) still produces a
    single-line, length-capped comment — the activity feed stays
    readable, and a runaway traceback can't dominate the thread."""
    class LoudTransport(RecordingTransport):
        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            # 1000-char message — the truncation caps the comment at 300.
            raise ValueError("boom: " + ("x" * 1000))

    transport = LoudTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="loud-explode", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, card.id)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # The 300-char cap keeps the comment from absorbing a runaway exception
    # verbatim; the "..." marker tells the reader it's truncated.
    assert "..." in failure_comments[-1]


@pytest.mark.asyncio
async def test_reaper_dead_on_arrival_impediment_keeps_legacy_fallback():
    """The reap path (`_release_dead_claim`'s dead-on-arrival branch)
    doesn't see the original spawn exception — the session was spawned
    successfully, then died. Without a captured pane the comment must
    keep the legacy "Check the backend logs" hint so operators know where
    to look. Bounds the no-last-error branch of `_move_to_impediment_after_..`."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="reap-fallback", column="engineer")
        # Pre-arm dispatch_failures so the *next* do-a reap pushes the card
        # past MAX_DISPATCH_FAILURES instead of just bumping the counter.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_failures":
                                     dispatch.MAX_DISPATCH_FAILURES - 1},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-reap-fallback"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS - 5)
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # Reap path has no last_error → falls back to the legacy hint.
    assert "Check the backend logs" in failure_comments[-1]


@pytest.mark.asyncio
async def test_synchronous_spawn_failure_clears_stale_resume_fields(monkeypatch):
    # The card has resume_session_id set, so get_transport_for_card always picks
    # the resume transport over the `transport` passed to dispatch_project (see
    # get_transport_for_card) -- patch make_resume_transport itself so the failure
    # is deterministic instead of depending on real ~/.claude/projects contents.
    transport = RecordingTransport(fail=True)
    monkeypatch.setattr(dispatch, "make_resume_transport", lambda *a, **k: transport)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume-spawn", column="To Resume")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-session",
                                     "resume_project_folder": "-old-worktree"},
        )
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1


@pytest.mark.asyncio
async def test_reap_resets_failure_streak_after_long_running_claim():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recovering", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_failures": 2},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-recover-0001"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS + 10)
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert card.dispatch_failures == 0


@pytest.mark.asyncio
async def test_run_dispatch_tick_commits_compensating_ops_on_spawn_failure(monkeypatch):
    """Regression test for a real bug found live on this project's own board: a card
    stuck in "To Resume" with a resume_session_id pointing at a merged/GC'd worktree
    kept failing to spawn (ValueError from resolve_directory) every ~10s tick,
    forever, with the card ending each cycle in *exactly* the state it started --
    no failure count, no cleared resume pointer, not even the claim released.

    Root cause: run_dispatch_tick's per-project `except Exception:` branch logged
    the failure but never called `ks.commit()`. _run_card's except block *does*
    apply compensating ops (release the claim, clear the stale resume pointer, bump
    dispatch_failures, move back / to Impediment) before re-raising, but those were
    only flushed, not committed -- the `async with KanbanSessionLocal()` block's
    implicit close-without-commit silently discarded all of them. This test exercises
    the real run_dispatch_tick entrypoint (not dispatch_project directly, which is
    what the other spawn-failure tests use and why this bug went unnoticed) and
    asserts the compensating ops actually persist."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=[PK]))
    monkeypatch.setattr(dispatch, "match_project_paths", lambda *a, **kw: {PK: "/p"})
    monkeypatch.setattr(dispatch, "_live_sessions", lambda: set())
    monkeypatch.setattr(dispatch, "_live_sandcastle_sessions",
                        mock.AsyncMock(return_value=set()))

    transport = RecordingTransport(fail=True)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="always-fails", column="Backlog")
        await s.commit()

    await dispatch.run_dispatch_tick(transport=transport)

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Backlog"        # compensating move-back was committed
    assert card.claimed_by is None         # compensating release was committed
    assert card.dispatch_failures == 1     # circuit-breaker counter was committed
    assert len(transport.calls) == 1


# ---- portfolio-cap: gate the sum of agent-claims across all projects -------


async def _make_claimed_agent_card(s, project_key, session_name):
    """Create a card, move it into an agent column with an `agent:` claim so it
    counts toward _active_session_count for `project_key`."""
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=project_key,
        entity_id=None, payload={"title": "busy", "column": "Backlog"},
    )
    await s.flush()
    card = await get_card(s, cid)
    card.column = "engineer"
    card.claimed_by = f"agent:{session_name}"
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_run_dispatch_tick_skips_when_portfolio_cap_reached(monkeypatch, caplog):
    """5 autodispatch projects each holding 1 agent-claim (total 5) with cap=4:
    the whole tick is skipped before any per-project dispatch runs."""
    import logging
    import unittest.mock as mock

    import app.kanban.db as kdb

    keys = [f"git:example.com/me/repo{i}" for i in range(5)]
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "_retry_queued_cards", mock.AsyncMock())
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=keys))
    monkeypatch.setattr(dispatch, "_registered_project_paths",
                        mock.AsyncMock(return_value=["/p"]))
    match_mock = mock.Mock(return_value={keys[0]: "/p"})
    monkeypatch.setattr(dispatch, "match_project_paths", match_mock)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_enabled", True)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_value", 4)

    async with KanbanSessionLocal() as s:
        for i, key in enumerate(keys):
            await _make_claimed_agent_card(s, key, session_name=f"s{i}")
        # A dispatchable card that would be spawned if the tick weren't skipped.
        await _make_card(s, title="pending", column="Backlog")
        await s.commit()

    transport = RecordingTransport()
    with caplog.at_level(logging.INFO, logger="app.kanban.dispatch"):
        await dispatch.run_dispatch_tick(transport=transport)

    assert len(transport.calls) == 0          # returned before the dispatch loop
    match_mock.assert_not_called()            # never reached path resolution
    assert "portfolio-cap reached (5/4 active sessions across 5 projects)" in caplog.text


@pytest.mark.asyncio
async def test_run_dispatch_tick_ignores_portfolio_cap_when_disabled(monkeypatch):
    """With the feature flag off, the same 5-claims-over-cap-4 state does not
    short-circuit the tick — a pending card in an enabled project is dispatched."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    keys = [f"git:example.com/me/repo{i}" for i in range(5)]
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "_retry_queued_cards", mock.AsyncMock())
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=keys))
    monkeypatch.setattr(dispatch, "_registered_project_paths",
                        mock.AsyncMock(return_value=["/p"]))
    monkeypatch.setattr(dispatch, "match_project_paths",
                        lambda *a, **kw: {keys[0]: "/p"})
    monkeypatch.setattr(dispatch, "_live_sessions", lambda: set())
    monkeypatch.setattr(dispatch, "_live_sandcastle_sessions",
                        mock.AsyncMock(return_value=set()))
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_enabled", False)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_value", 4)

    async with KanbanSessionLocal() as s:
        for i, key in enumerate(keys):
            await _make_claimed_agent_card(s, key, session_name=f"s{i}")
        # Pending card under the one project that maps to a local path, so the
        # tick has something to dispatch once it does not short-circuit.
        await apply_operation(
            s, op_type="create", entity_type="card", project_key=keys[0],
            entity_id=None, payload={"title": "pending", "column": "Backlog"},
        )
        await s.commit()

    transport = RecordingTransport()
    await dispatch.run_dispatch_tick(transport=transport)

    assert len(transport.calls) >= 1          # dispatch proceeded despite 5 claims


# ---- stuck-session reaper (alive in tmux, never sent hooks, 429/Token Plan
# in pane content -> set dispatch_pause, kill, release) ----------------------


class _FrozenClock:
    """Test clock for SessionRegistry's monotonic timer. Advancing moves
    spawn ages past the stuck timeout so the registry actually surfaces
    the name from get_stuck_sessions()."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_reaper_reaps_stuck_session_with_429_and_pauses_dispatch(monkeypatch):
    """A session that's alive in tmux but never sent a hook (classic 429
    Token Plan signature: `claude` prints the error and never initialises
    hooks) must be killed by the reaper, its claim released, dispatch_failures
    bumped, and the global dispatch pause set to the fallback duration.
    Without this, the card sits claimed forever and auto-dispatch stalls."""
    from datetime import UTC, datetime, timedelta

    import app.kanban.dispatch as d
    from app.kanban import dispatch_pause
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-429-0001")
    clock.advance(200)  # past the default 120s stuck threshold
    monkeypatch.setattr(d, "session_registry", reg)

    # Mock capture-pane to simulate a 429 stuck tmux pane.
    pane = "API Error: 429 — Token Plan limit reached for this account"
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: pane,
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-429-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-429-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)
        paused_until = await dispatch_pause.get_paused_until(s)

    assert reaped == 1
    assert killed == ["k-429-0001"]
    assert card.claimed_by is None
    assert card.dispatch_failures == 1
    # The 429 path now pauses per-provider (anthropic by default -- no
    # column default in this test, no override) rather than the legacy
    # global slot. The legacy global slot is intentionally untouched so
    # other providers' traffic is not collateral-frozen.
    assert paused_until is None
    async with KanbanSessionLocal() as s2:
        paused_provider = await dispatch_pause.get_paused_until(
            s2, provider="anthropic",
        )
    assert paused_provider is not None
    # FALLBACK_PAUSE_HOURS = 5 — accept any wall-clock drift up to 60s.
    expected = datetime.now(UTC) + timedelta(hours=5)
    assert abs((paused_provider - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_cleanup_stuck_rate_limited_session_posts_comment(monkeypatch):
    """When the stuck-session reaper reaps a 429 session, an activity comment
    on the card surfaces what happened and why the card was released -- the
    'tmux killed, claim released, dispatch paused for ~5h' lifecycle is otherwise
    invisible from the activity feed."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-429-comment")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    pane = "API Error: 429 — Token Plan limit reached for this account"
    monkeypatch.setattr(d, "_capture_pane_content", lambda name, *, lines=20: pane)
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-429-comment", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-429-comment"},
        )
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-429-comment"}, project_path="/p",
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert any("Stuck session" in t for t in comment_texts)
    assert any("429" in t for t in comment_texts)
    assert any("~5h" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_reaper_skips_stuck_session_without_rate_limit(monkeypatch):
    """A session that's alive in tmux but just slow to send hooks (the pane
    shows ordinary work, no 429) must NOT be killed by the new reaper path —
    we'd otherwise silently lose a healthy in-flight session."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-clean-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "Working on it…",
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-clean", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-clean-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-clean-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0
    assert killed == []
    assert card.claimed_by == "agent:k-clean-0001"  # untouched


@pytest.mark.asyncio
async def test_reaper_stuck_session_fails_open_when_capture_pane_unavailable(monkeypatch):
    """If `capture-pane` itself fails (tmux not on PATH, timeout, …), the
    reaper must not act on the stuck session — fail-open is safer than
    killing a session whose pane we can't actually read."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-failopen-1")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # capture-pane returns None = failure to capture
    monkeypatch.setattr(d, "_capture_pane_content", lambda name, *, lines=20: None)
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-no-capture", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-failopen-1"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-failopen-1"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0
    assert killed == []
    assert card.claimed_by == "agent:k-failopen-1"


@pytest.mark.asyncio
async def test_reaper_stuck_session_clears_resume_fields(monkeypatch):
    """When a 429-stuck session is reaped, any stale resume_session_id /
    resume_project_folder pointing at a since-merged worktree must be
    cleared too — otherwise the next dispatch picks the resume transport
    and re-spawns against a dead worktree, hitting the same 429 again."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-stale-429")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    monkeypatch.setattr(
        d, "_capture_pane_content",
        lambda name, *, lines=20: "API Error: 429 - Token Plan",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume-429", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-sess",
                                     "resume_project_folder": "-old-worktree"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stale-429"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-stale-429"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_reaper_stuck_session_repeated_failures_move_to_impediment(monkeypatch):
    """A card that hits a 429 three ticks in a row must end up in Impediment
    (same circuit breaker as the dead-on-arrival path), so a human can
    look at it instead of the loop burning dispatch ticks forever."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    monkeypatch.setattr(d, "session_registry", reg)
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "API Error: 429",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="repeat-429", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        # Simulate a fresh spawn that has now been alive past the stuck
        # threshold: mark first (captures the current clock), then advance
        # so the reap sees the spawn age as >= timeout_s.
        reg.clear_spawn("k-imp-0001")
        reg.mark_spawned("k-imp-0001")
        clock.advance(200)
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-imp-0001"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions={"k-imp-0001"}, project_path="/p",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0  # reset so a future redispatch starts fresh
    # tagged so the board renders it red — a technical dispatch failure, not a
    # human-parked impediment (see dispatch.ERROR_LABEL / CardItem.tsx)
    assert dispatch.ERROR_LABEL in (card.labels or [])


@pytest.mark.asyncio
async def test_reaper_stuck_session_impediment_comment_includes_pane(monkeypatch):
    """When a 429 rate-limit session trips MAX_DISPATCH_FAILURES, the
    dispatch-failure auto-move comment must surface the captured pane
    content (`API Error: 429 …`) so the operator sees the actual rate-
    limit reason — not just "Check the backend logs". See kanban card
    5ec5a68013da4422b0a49fb2731cb8a7."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    monkeypatch.setattr(d, "session_registry", reg)
    monkeypatch.setattr(
        d, "_capture_pane_content",
        lambda name, *, lines=20: "API Error: 429 rate limit reached",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="repeat-429-with-pane", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        reg.clear_spawn("k-imp-0002")
        reg.mark_spawned("k-imp-0002")
        clock.advance(200)
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-imp-0002"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions={"k-imp-0002"}, project_path="/p",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    assert "API Error: 429" in failure_comments[-1]


def test_capture_pane_content_returns_pane_text(monkeypatch):
    """_capture_pane_content shells out to `tmux capture-pane` and returns
    stdout. Verifies the cmd shape (session name + tail lines) since
    regressions there would silently shift what content the rate-limit
    detector sees."""
    import app.kanban.dispatch as d

    seen = []

    class R:
        returncode = 0
        stdout = "Working..."
        stderr = ""

    def fake_run(cmd, *a, **k):
        seen.append(cmd)
        return R()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    out = d._capture_pane_content("k-test", lines=20)
    assert out == "Working..."
    assert seen[0][0:3] == ["tmux", "capture-pane", "-t"]
    assert seen[0][3] == "k-test"
    assert "-S" in seen[0]
    assert "-20" in seen[0]


def test_capture_pane_content_returns_none_on_failure(monkeypatch):
    """If tmux capture-pane fails (session gone, non-zero exit, FileNotFound,
    timeout) the helper returns None so the reaper fails open. Returning
    a partial/empty string would silently downgrade the detector."""
    import app.kanban.dispatch as d

    class RFail:
        returncode = 1
        stdout = ""
        stderr = "can't find pane"

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: RFail())
    assert d._capture_pane_content("k-missing") is None

    def raise_fnf(*a, **k):
        raise FileNotFoundError("tmux not on PATH")

    monkeypatch.setattr(d.subprocess, "run", raise_fnf)
    assert d._capture_pane_content("k-missing") is None


def test_is_rate_limited_session_matches_known_patterns():
    """_is_rate_limited_session must recognise real Claude/MiniMax limit
    messages, but not raw pane text that just happens to mention '429',
    'api error', or 'request rejected' in unrelated HTTP/test output — see
    kanban card 3a8f27a4… which traced the 2026-07-22 false-positive on a
    healthy session (an agent doing curl/HTTP tests left a '429' in pane
    history) back to those loose single-token needles.

    The pane-scan gets raw terminal output (curl results, error logs from
    any third-party API the agent is testing), so its needles are tighter
    than the Notification classifier's — a Notification payload is
    structured and comes from Claude Code itself, but a pane capture is
    whatever the terminal happened to render. Bare '429' or 'api error'
    alone is no longer enough; combinations are required for the generic
    HTTP-error phrases."""
    import app.kanban.dispatch as d
    # Canonical Anthropic messages — single exact phrase matches.
    assert d._is_rate_limited_session(
        "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    ) is True
    assert d._is_rate_limited_session(
        "You've hit your weekly limit · resets 9pm (Europe/Brussels)"
    ) is True
    # Canonical MiniMax messages — 'token plan' alone is specific enough.
    assert d._is_rate_limited_session(
        "API Error: Request rejected (429) · Token Plan usage limit reached"
    ) is True
    # Generic HTTP 429 — needs both 'api error' and '429' AND a rate/toomany
    # context word to disambiguate from a healthy agent doing HTTP tests.
    assert d._is_rate_limited_session("API Error: 429 Too Many Requests") is True
    # Negative cases — these must NOT trip the detector (the regression cases
    # from the 2026-07-22 false-positive incident).
    assert d._is_rate_limited_session(
        "$ curl -i https://api.example.com/v1/usage\nHTTP/2 429\n"
    ) is False
    assert d._is_rate_limited_session(
        "Some API error: invalid request format"
    ) is False
    assert d._is_rate_limited_session(
        "Request rejected: invalid API key"
    ) is False
    # The literal pane content from the bug report — an in-flight session
    # displaying a PLAN CONTEXT block + plan-deliverable id + parent-card id
    # + 'Two findings already. Let me fix the endpoint path...'.
    assert d._is_rate_limited_session(
        "❯ PLAN CONTEXT — read this first Plan deliverable: "
        "27033503b1ad43d49ddca47548dacfce\n"
        " Parent card: 38d32e94c0484d7ca0a4b09dccc22e42 "
        "● Two findings already. Let me fix the\n"
        " endpoint path and register the "
    ) is False
    # Existing negative cases — must still NOT match.
    assert d._is_rate_limited_session("Working on tests...") is False
    assert d._is_rate_limited_session("Planning the next refactor") is False
    assert d._is_rate_limited_session("") is False
    assert d._is_rate_limited_session("Compaction 1/2 complete") is False


def test_find_rate_limit_match_returns_matched_phrase():
    """_find_rate_limit_match returns the specific needle/phrase that
    classified a hit, so the reaper can log *why* it acted. Without this
    an operator chasing a false-positive has only the truncated 200-char
    pane prefix to work with, which is exactly the observability gap the
    card called out ('which needle matched is not to find out')."""
    import app.kanban.dispatch as d
    # Canonical single-phrase matches.
    assert d._find_rate_limit_match(
        "You've hit your session limit · resets 11pm"
    ) == "hit your session limit"
    assert d._find_rate_limit_match(
        "You've hit your weekly limit · resets 9pm"
    ) == "hit your weekly limit"
    assert d._find_rate_limit_match(
        "API Error: Request rejected (429) · Token Plan usage limit reached"
    ) == "token plan"
    # Combo match — 'api error + 429' is the tightest combo that catches
    # the canonical MiniMax reject format. Tokens are joined in sorted
    # order so the returned phrase is stable for log assertions.
    assert d._find_rate_limit_match(
        "API Error: 429 Too Many Requests"
    ) == "429+api error"
    # Combo match — 'request rejected + 429' (no 'rate' / 'limit' word
    # needed; the 429 status itself is the disambiguator).
    assert d._find_rate_limit_match(
        "Request rejected (429) — try again later"
    ) == "429+request rejected"
    # Non-matches — must return None, not a guessed phrase.
    assert d._find_rate_limit_match(
        "$ curl -i https://api.example.com/v1/usage\nHTTP/2 429\n"
    ) is None
    assert d._find_rate_limit_match(
        "Request rejected: invalid API key"
    ) is None
    assert d._find_rate_limit_match("") is None
    # Case insensitivity — capture-pane text is whatever the terminal
    # rendered, including any ANSI colour escapes; the matcher must work
    # without us stripping case.
    assert d._find_rate_limit_match(
        "YOU'VE HIT YOUR SESSION LIMIT · resets 11pm"
    ) == "hit your session limit"


# ---- pane-scan scoped to pre-transcript case (card 3a8f27a4… R4) ------------
#
# Once the transcript-tail detector (`detect_transcript_rate_limits`) is the
# authoritative mid-session source (R1 / `c8ad1ea8…`), the pane substring
# scan should only fire when there is no usable transcript yet — the case
# it was originally built for (a `claude` process that printed a 429 and
# died before its hooks ever fired). When a transcript exists, the
# transcript detector is leading; running the pane scan on top of it is
# what killed the healthy k-spike-meet-wa-d450 session on 2026-07-22.


@pytest.mark.asyncio
async def test_reaper_pane_scan_skipped_when_transcript_exists(monkeypatch, tmp_path):
    """When a stuck session's transcript file already exists with content,
    the reaper must NOT run the pane substring-scan fallback: that path is
    the mid-session detector's job. Without this guard, an agent running
    curl/HTTP tests whose history happens to mention '429' gets killed by
    a false positive even though its transcript is the authoritative source.

    Reproduces the 2026-07-22 false-positive incident (card 3a8f27a4…,
    logs/backend/run-20260722-095619-2861-0.log:947): the session was
    actively working ('Two findings already. Let me fix the endpoint
    path…') when the pane substring-scan flagged it for cleanup."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-spike-meet-wa-d450")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # Pane content contains only the literal text from the bug report's
    # log line — no 429/api error context at all. Even if it did, the
    # transcript detector is the leading source for this session.
    pane_with_false_positive_risk = (
        "❯ PLAN CONTEXT — read this first Plan deliverable: "
        "27033503b1ad43d49ddca47548dacfce\n"
        " Parent card: 38d32e94c0484d7ca0a4b09dccc22e42 "
        "● Two findings already. Let me fix the\n"
        " endpoint path and register the "
    )
    monkeypatch.setattr(
        d, "_capture_pane_content",
        lambda name, *, lines=20: pane_with_false_positive_risk,
    )

    # Pretend the session has an existing transcript — i.e. it's been
    # productive for a while and the transcript detector would have seen
    # any actual limit. The reaper must skip the pane scan entirely.
    fake_transcript = tmp_path / "transcript.jsonl"
    fake_transcript.write_text(
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"ordinary work, no limit"}]}}\n'
    )

    monkeypatch.setattr(
        srec, "_resolve_transcript_file", lambda project_path, session_name, **kw: fake_transcript,
    )

    # Even if the matcher would say "limit", we should NOT kill — transcript
    # is the source of truth for mid-session limits.
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="transcript-exists", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-spike-meet-wa-d450"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-spike-meet-wa-d450"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0
    assert killed == []
    assert card.claimed_by == "agent:k-spike-meet-wa-d450"


@pytest.mark.asyncio
async def test_reaper_pane_scan_still_fires_for_fresh_429_when_no_transcript(monkeypatch, tmp_path):
    """The pane-scan still catches the classic 429-on-first-spawn case
    (R4's pre-transcript-only contract): `claude` prints the error and
    dies before hooks fire, so the transcript file doesn't exist yet.
    The tight needles recognise the canonical MiniMax error and trigger
    cleanup."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-fresh-429-001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    pane = "API Error: Request rejected (429) · Token Plan usage limit reached"
    monkeypatch.setattr(d, "_capture_pane_content", lambda name, *, lines=20: pane)

    # Transcript file does NOT exist — fresh spawn died before any
    # transcript was written.
    monkeypatch.setattr(
        srec, "_resolve_transcript_file",
        lambda project_path, session_name, **kw: None,
    )

    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="fresh-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-fresh-429-001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-fresh-429-001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert killed == ["k-fresh-429-001"]
    assert card.claimed_by is None


# ---- structured-signal fast path (acp-transport-decision.md §6 kaart 2) -----
#
# The reaper previously inspected tmux pane content for 429 substrings. The
# card above (§6 kaart 1 / orchestration-substrate §6 kaart 2) replaces that
# with typed Notification-classification signals recorded by the hook
# endpoint. The tests below verify the fast path works and the pane-scan
# fallback still kicks in for sessions that never fired a hook.


@pytest.mark.asyncio
async def test_reaper_stuck_session_uses_structured_signal_when_recorded(monkeypatch):
    """The structured-signal fast path: when a Notification(kind=limit) has
    already been recorded for the stuck session, the reaper must trigger
    the full cleanup (kill tmux + dispatch pause + dispatch_failures bump)
    without needing to scrape the pane. The pane may have been cleared by
    the time the reaper runs, so a real-world test would have the capture
    return None or unrelated text — we assert the structured signal alone
    is enough to act."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg
    from app.services.scheduling import session_signals as ssignals

    ssignals.session_signals.clear("k-struct-0001")
    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-struct-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # Record the structured signal before the reaper runs — this is the
    # normal lifecycle: Notification hook fires, classify says "limit",
    # registry records, then the reaper sweeps on its next tick.
    ssignals.session_signals.record_limit(
        "/p/.claude/worktrees/k-struct-0001",
        "API Error: 429 rate limit reached",
    )
    # Pane scrape would return unrelated text or fail — the structured
    # signal must still drive the cleanup.
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: None,
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-struct", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-struct-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-struct-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert killed == ["k-struct-0001"]
    assert card.claimed_by is None  # claim released by the cleanup path
    assert card.dispatch_failures == 1
    ssignals.session_signals.clear("k-struct-0001")


@pytest.mark.asyncio
async def test_reaper_stuck_session_still_falls_back_to_pane_without_signal(monkeypatch):
    """The fail-open path: when no structured signal has been recorded (the
    classic 429-on-first-spawn case where the `claude` process died before
    initialising hooks), the reaper must still catch the rate-limit via the
    pane substring-match — that's the entire reason the pane scrape
    survived this refactor."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg
    from app.services.scheduling import session_signals as ssignals

    ssignals.session_signals.clear("k-pane-0001")
    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-pane-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)
    # No structured signal recorded — session never fired a hook.
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "API Error: 429",
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-pane", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-pane-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-pane-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert killed == ["k-pane-0001"]
    assert card.dispatch_failures == 1
    ssignals.session_signals.clear("k-pane-0001")


# ---- _session_has_transcript tri-state + non-Claude reaper guard ----------
# (kaart 55fa66d1…). Before the per-CLI routing, the resolver always asked
# Claude for a transcript on a Codex/OpenCode/MiMo/Copilot worktree, always
# got None, and the reaper read that as "no transcript yet — fire the pane
# scan" even on a productive mid-session agent whose pane happened to
# mention '429' from a curl probe. The tri-state makes "no signal"
# distinguishable from "no transcript" and the reaper's `is False` gate
# keeps the pane scan suppressed for the unsupported-CLI case.


def test_session_has_transcript_true_when_transcript_has_content(monkeypatch, tmp_path):
    """Claude with a non-empty transcript → True (drop through, transcript
    detector owns mid-session limits)."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec

    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')

    monkeypatch.setattr(
        srec, "_resolve_transcript_file",
        lambda project_path, session_name, *, cli_id, **kw: transcript,
    )

    assert d._session_has_transcript("/p", "k-x", cli_id="claude-code") is True


def test_session_has_transcript_false_when_resolver_returns_none(monkeypatch, tmp_path):
    """Claude, no transcript on disk → False (run the pane scan, classic
    429-on-first-spawn case)."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec

    monkeypatch.setattr(
        srec, "_resolve_transcript_file",
        lambda project_path, session_name, *, cli_id, **kw: None,
    )

    assert d._session_has_transcript("/p", "k-x", cli_id="claude-code") is False


def test_session_has_transcript_none_for_unsupported_cli(monkeypatch, tmp_path):
    """Non-Claude CLI (Copilot, today) → None (no signal — suppress the
    pane scan rather than fire it). The reaper's `is False` gate is the
    only thing that keeps a productive Codex session whose pane happens
    to mention '429' from being reaped (kaart 55fa66d1…)."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec

    monkeypatch.setattr(
        srec, "_resolve_transcript_file",
        lambda project_path, session_name, *, cli_id, **kw: None,
    )

    assert d._session_has_transcript("/p", "k-x", cli_id="copilot-cli") is None
    assert d._session_has_transcript("/p", "k-x", cli_id="codex-cli") is None
    assert d._session_has_transcript("/p", "k-x", cli_id="open-code") is None
    assert d._session_has_transcript("/p", "k-x", cli_id="mimo-code") is None


def test_session_has_transcript_false_when_project_path_missing(monkeypatch):
    """Defensive: missing project_path → False (no scan-coordination
    possible). The reaper only reaches `_session_has_transcript` when
    ``project_path`` is set, but the function still has to answer
    sensibly for the no-context callers."""
    import app.kanban.dispatch as d

    assert d._session_has_transcript(None, "k-x", cli_id="claude-code") is False


@pytest.mark.asyncio
async def test_reaper_pane_scan_suppressed_for_non_claude_session(monkeypatch, tmp_path):
    """Regression for kaart 55fa66d1…: a Codex/OpenCode/MiMo/Copilot
    session whose pane happens to mention a 429 substring (e.g. from a
    curl probe or a quoted error in its work) must NOT be reaped by the
    pane substring-scan fallback — the resolver returns no signal, and
    the reaper's tri-state gate keeps the scan suppressed.

    Reproduces the false-positive class for the reaper when the lane
    isn't Claude Code: the resolver used to ask Claude for a transcript
    on a non-Claude worktree, always got None, and the reaper always
    ran the pane scan. With the per-CLI routing, ``None`` is distinct
    from ``False`` and the gate skips the scan."""
    import app.kanban.dispatch as d
    from app.kanban import session_recovery as srec
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-codex-pane-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # Pane content has a curl-probe-style 429 substring — exactly the
    # kind of legitimate content a productive Codex session can print
    # while running an HTTP smoke test against its own dev stack.
    pane = (
        "$ curl -i http://localhost:8000/api/v1/projects\n"
        "HTTP/2 429\n"
        "  retry-after: 30\n"
    )
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: pane,
    )

    # No usable transcript signal for Codex — the resolver returns
    # None, the reaper reads it as the unsupported-CLI "no signal"
    # branch and must NOT fire the pane scan.
    monkeypatch.setattr(
        srec, "_resolve_transcript_file",
        lambda project_path, session_name, *, cli_id, **kw: None,
    )

    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="codex-productive", column="engineer")
        await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=cid, payload={
                "executor_agent_id": "codex-cli",
                "agent": "codex-cli",
            },
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-codex-pane-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-codex-pane-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0, (
        "non-Claude session reaped by the pane scan despite no usable "
        "transcript signal — that's the false-positive class kaart "
        "55fa66d1… was filed to prevent"
    )
    assert killed == []
    assert card.claimed_by == "agent:k-codex-pane-0001"


# ---- post_agent_status_comment (CC 2.1.198+ background-agent notifications) -


@pytest.mark.asyncio
async def test_post_agent_status_comment_writes_to_claimed_card(monkeypatch):
    """A `agent_needs_input` / `agent_completed` notification for a
    kanban-dispatched session lands as a comment op on the card claimed
    by that session. The card itself is NOT moved (no `move` op emitted,
    column unchanged)."""
    import unittest.mock as mock

    from sqlalchemy import select

    from app.kanban import db as kdb
    from app.kanban.models import KanbanOp

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="background agent card", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-bg-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-bg-0001", "Session reported completion",
        )

    assert result is True

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        comment_rows = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == cid)
            .where(KanbanOp.op_type == "comment")
        )).scalars().all()
        move_rows = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == cid)
            .where(KanbanOp.op_type == "move")
        )).scalars().all()

    # Card column is untouched; the only side effect is the activity comment.
    assert card.column == "engineer"
    assert [r.payload.get("text") for r in comment_rows] == [
        "Session reported completion",
    ]
    assert move_rows == []


@pytest.mark.asyncio
async def test_post_agent_status_comment_ignores_non_worktree_cwd():
    """A cwd that isn't a `<project>/.claude/worktrees/<name>` shape (a
    manual `claude` session, sandcastle, project root) must not be
    touched. Same contract as ``move_limited_session_to_resume`` — the
    hook path is a no-op for non-kanban sessions."""
    result = await dispatch.post_agent_status_comment(
        "/home/me/some-project", "Session is waiting for input",
    )
    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_returns_false_when_no_matching_card(monkeypatch):
    """No card claimed by that session -> no-op, even if the cwd shape matches."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await _make_card(s, title="unrelated", column="engineer")
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-no-such-session", "Session reported completion",
        )

    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_returns_false_when_project_key_unresolved():
    """Unresolvable project path -> bail out before touching the DB."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=None
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-unknown-0001", "Session is waiting for input",
        )

    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_skips_cards_in_terminal_columns(monkeypatch):
    """Cards already on Done / To Resume must not receive a fresh
    'agent finished' comment — the operator has already declared an
    outcome, and re-commenting would be noise."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    for terminal_col in ("Done", "To Resume"):
        async with KanbanSessionLocal() as s:
            cid = await _make_card(
                s, title=f"finished card in {terminal_col}",
                column=terminal_col,
            )
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": f"agent:k-term-{terminal_col}"},
            )
            await s.commit()

        with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
            result = await dispatch.post_agent_status_comment(
                f"/p/.claude/worktrees/k-term-{terminal_col}",
                "Session reported completion",
            )

        assert result is False, (
            f"post_agent_status_comment must skip cards on {terminal_col}"
        )



# ---- child-card plan_ref dispatch gate (create_card→add_plan_attachment race) --

async def _make_child(s, *, parent_card_id, title="child", column="Backlog"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload={"title": title, "column": column,
                 "parent_card_id": parent_card_id},
    )
    await s.flush()
    return cid


async def _link_plan_ref(s, *, child_id, parent_id, plan_deliverable_id="plan-1"):
    import json
    await apply_operation(
        s, op_type="link_plan_ref", entity_type="deliverable",
        project_key=PK, entity_id=child_id,
        payload={"ref_json": json.dumps({
            "parent_card_id": parent_id,
            "plan_deliverable_id": plan_deliverable_id,
        }), "depends_on": []},
    )
    await s.flush()


@pytest.mark.asyncio
async def test_child_without_plan_ref_is_not_dispatched():
    """Race case: the analyst created a child (create_card) but hasn't attached
    the plan yet (add_plan_attachment). The child must NOT be dispatched — it
    would otherwise get the 'Plan niet beschikbaar' placeholder prompt."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        child_card = await get_card(s, child)
    assert transport.calls == []            # nothing spawned
    assert child_card.column == "Backlog"   # child stayed put, unclaimed
    assert not child_card.claimed_by


@pytest.mark.asyncio
async def test_child_with_plan_ref_is_dispatched():
    """Once add_plan_attachment has linked the plan_ref, the same child becomes
    dispatch-eligible and is spawned normally."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await _link_plan_ref(s, child_id=child, parent_id=parent)
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        child_card = await get_card(s, child)
    assert len(transport.calls) == 1        # child got spawned
    assert child_card.column != "Backlog"   # moved into an agent column
    assert child_card.claimed_by


@pytest.mark.asyncio
async def test_next_card_gate_distinguishes_race_from_genuine_miss():
    """The plan_ref gate keeps the race case (plan attached moments later) out of
    dispatch, while the genuine-miss placeholder path is only reached by a child
    that DOES hold a plan_ref pointing at a now-missing parent/plan."""
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        # Race case: child without plan_ref -> gated, _next_card skips it.
        raced = await _make_child(s, parent_card_id=parent, title="raced")
        await s.commit()
        cards = await list_cards(s, PK)
        raced_card = next(c for c in cards if c.id == raced)
        assert dispatch._awaiting_plan_ref(raced_card) is True
        assert dispatch._next_card([raced_card]) is None

        # Genuine-miss case: child holds a plan_ref, but the parent is gone.
        await _link_plan_ref(
            s, child_id=raced, parent_id="deleted-parent",
            plan_deliverable_id="gone",
        )
        await s.commit()

    # Re-query with a fresh session, mirroring how a real dispatch tick always
    # opens a new `KanbanSessionLocal()` (see `run_dispatch_tick`). Reusing `s`
    # above would serve `missed_card.deliverables` from the identity map's
    # already-loaded (pre-link) collection instead of the just-committed row,
    # since `expire_on_commit=False` never invalidates already-loaded
    # relationships without an explicit `expire`/`refresh`.
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        missed_card = next(c for c in cards if c.id == raced)
        # No longer gated — it IS eligible now (plan_ref present).
        assert dispatch._awaiting_plan_ref(missed_card) is False
        # ...and resolving its plan yields a DANGLING_PARENT status
        # (the parent_id in the ref was "deleted-parent" which never existed).
        plan_status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, missed_card,
        )
    assert plan_status == dispatch.PLAN_DANGLING_PARENT
    assert plan_md is None
    assert plan_id == "gone"
    assert parent_id == "deleted-parent"
    section = dispatch._plan_context_section(
        status=plan_status,
        plan_markdown=plan_md,
        plan_deliverable_id=plan_id,
        parent_card_id=parent_id,
        # The child in this test was created without a description; the
        # softened-guidance path requires a non-empty description.
        card_description="",
    )
    assert "Plan niet beschikbaar" in section
    assert "deleted-parent" in section


# ---- card 4a03565d: status-aware plan resolution + softened guidance -------

@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_dangling_parent_status():
    """A child with plan_ref whose parent_card_id points at a non-existent
    card must return PLAN_DANGLING_PARENT (not the generic (None,None,None))."""
    async with KanbanSessionLocal() as s:
        # Note: no parent card created — parent_id "ghost-parent" is dangling.
        child = await _make_child(s, parent_card_id="ghost-parent", title="child")
        await _link_plan_ref(
            s, child_id=child, parent_id="ghost-parent",
            plan_deliverable_id="plan-xyz",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_DANGLING_PARENT
    assert plan_md is None
    assert plan_id == "plan-xyz"
    assert parent_id == "ghost-parent"


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_plan_missing_on_parent_status():
    """A child with plan_ref whose parent exists but lacks the referenced
    plan deliverable must return PLAN_MISSING_ON_PARENT (not a generic
    failure that gets mistaken for 'parent deleted')."""
    async with KanbanSessionLocal() as s:
        # Create a real parent but DO NOT add a plan deliverable to it.
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await _link_plan_ref(
            s, child_id=child, parent_id=parent,
            plan_deliverable_id="plan-id-not-on-parent",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_MISSING_ON_PARENT
    assert plan_md is None
    assert plan_id == "plan-id-not-on-parent"
    assert parent_id == parent


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_no_plan_ref_status():
    """A child card with no plan_ref deliverable at all must return
    PLAN_NO_REF. Mirrors the race-window case where the analyst hasn't
    attached the plan yet — but here we exercise the leaf helper directly
    because _awaiting_plan_ref already gates dispatch on plan_ref presence."""
    async with KanbanSessionLocal() as s:
        # Create parent + child but skip _link_plan_ref entirely.
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_NO_REF
    assert plan_md is None
    assert plan_id is None
    assert parent_id is None


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_malformed_status_for_bad_json():
    """A child whose plan_ref ref is not parseable JSON must surface
    PLAN_MALFORMED instead of being silently swallowed as (None,None,None)."""
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await apply_operation(
            s, op_type="link_plan_ref", entity_type="deliverable",
            project_key=PK, entity_id=child,
            payload={"ref_json": "not-json-{", "depends_on": []},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_MALFORMED
    assert plan_md is None
    assert plan_id is None
    assert parent_id is None


def test_plan_context_section_dangling_parent_distinguishes_from_no_ref():
    """The PLAN_DANGLING_PARENT message must mention the specific parent
    id, not collapse into 'mogelijk is de parent verwijderd of is het plan
    nooit opgeslagen' — that's the bug card 4a03565d reported."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-cb29",
        parent_card_id="parent-d4d7",
        card_description="",
    )
    assert "parent-d4d7" in section
    assert "plan-cb29" in section
    # Old message bundled two cases into one; the new message must be
    # specific about WHICH case it is.
    assert "bestaat niet meer" in section or "nooit aangemaakt" in section
    assert "nooit opgeslagen" not in section, (
        "old fallback phrasing must not leak into the new message — "
        "this is the exact symptom from card 4a03565d"
    )


def test_plan_context_section_missing_on_parent_message_is_specific():
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_MISSING_ON_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-missing",
        parent_card_id="parent-alive",
        card_description="",
    )
    assert "parent-alive" in section
    assert "plan-missing" in section
    assert "niet (meer) op te vinden" in section


def test_plan_context_section_malformed_message_is_specific():
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_MALFORMED,
        plan_markdown=None,
        plan_deliverable_id=None,
        parent_card_id=None,
        card_description="",
    )
    assert "misvormd" in section
    assert "parseerbare JSON" in section


def test_plan_context_section_self_sufficient_card_does_not_force_impediment():
    """A card with a non-empty description is self-sufficient: the
    placeholder must guide the executor to proceed using the description
    and post a `**Self-improve:**` note, NOT unconditionally steer to
    report_impediment. This is the softening requirement from the
    acceptance criteria."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description=(
            "Wire ACP-isomorf structured events into agentic_cli. "
            "Source: docs/cockpit/acp-transport-decision.md §6."
        ),
    )
    # Self-sufficient path: steer via Self-improve comment, not impediment.
    assert "Self-improve" in section
    assert "kaartbeschrijving" in section
    # The `report_impediment` reference must still appear as a *fallback*,
    # not as the primary guidance — the executor should not see it as
    # the first action to take. We check it appears only after "ALLEEN".
    alleen_idx = section.find("ALLEEN")
    imp_idx = section.find("report_impediment")
    if imp_idx != -1:
        assert alleen_idx != -1 and imp_idx > alleen_idx, (
            "report_impediment must only appear as a fallback after ALLEEN, "
            "not as the primary instruction"
        )


def test_plan_context_section_empty_description_steers_to_impediment():
    """A card with no usable description has no source of truth besides
    the plan — guidance must steer to report_impediment immediately."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description="",
    )
    assert "report_impediment" in section
    assert "Self-improve" not in section


def test_plan_context_section_whitespace_only_description_steers_to_impediment():
    """A whitespace-only description is treated as empty (we strip() in
    the helper) — guidance must steer to report_impediment."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description="   \n\t  ",
    )
    assert "report_impediment" in section
    assert "Self-improve" not in section


# ---- per-provider pause for limit hits (kanban-limit feature) --------------

@pytest.mark.asyncio
async def test_provider_for_card_uses_per_column_override_when_present():
    """When the card carries a column_overrides[<agent>].provider, that wins --
    the per-provider pause must target the SAME subscription a fresh respawn
    would (otherwise the pause would mismatch the subscription that hit its
    429)."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}},
        )
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "bedrock"


@pytest.mark.asyncio
async def test_provider_for_card_falls_through_to_column_default():
    """No per-column override -> column default_provider wins."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_provider_for_card_falls_back_to_anthropic_when_nothing_configured():
    """No override, no column default -> the dispatcher's hard-coded
    PROVIDER_ANTHROPIC fallback (mirrors dispatch_card). A pause resolved here
    still targets anthropic specifically (the only subscription the fresh
    respawn would pick), not a global one."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "anthropic"


@pytest.mark.asyncio
async def test_provider_for_card_returns_none_when_inputs_insufficient():
    """If the caller hands in no card or no agent column, the helper refuses to
    guess a provider -- returning None so the caller can take the global-pause
    fallback rather than silently targeting anthropic."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    # No card -> None.
    async with KanbanSessionLocal() as s:
        assert await dispatch._provider_for_card(s, PK, None, "engineer") is None
    # No agent column -> None (a stale call, e.g. column already moved).
    async with KanbanSessionLocal() as s:
        assert await dispatch._provider_for_card(s, PK, card, "") is None


# ---- _limited_provider_for_card (kaart 9ff86416…) ------------------------
#
# The reactive limit path needs "the provider this card was authenticated
# against" — and ``_provider_for_card`` only walks the last two layers
# (column override → column default → PROVIDER_ANTHROPIC). When the dispatch
# resolved a different provider via global_override or a pool entry, that
# narrow resolver silently gates the wrong subscription. The new helper
# prefers ``card.dispatch_provider`` (the vendor the dispatcher actually
# picked at spawn time, kanban-card 8a2ad986…) and only falls back to the
# narrow resolver when both ``dispatch_provider`` is unset AND ``project_path``
# isn't available (legacy reaper path with no project_path context).

@pytest.mark.asyncio
async def test_limited_provider_for_card_prefers_dispatch_provider_when_set():
    """Card has a column default of anthropic but ``dispatch_provider``
    says it was actually spawned against minimax (pool/global_override
    routing). The helper MUST return minimax, not anthropic — otherwise
    the per-provider pause would gate the wrong subscription."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_provider="anthropic",
        )
        cid = await _make_card(s, title="dispatched-on-pool", column="analyst")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_provider": "minimax"},
        )
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._limited_provider_for_card(
            s, project_key=PK, project_path="/p", card=card,
            target_column="analyst",
        )

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_limited_provider_for_card_falls_back_to_effective_chain_when_no_dispatch_provider():
    """Legacy / never-dispatched card: ``dispatch_provider`` is None, so the
    helper walks the full precedence chain via
    ``_effective_provider_for_pause_gate`` — here a global_override pins to
    minimax while the column default is anthropic."""
    from app.kanban.dispatch import set_active_subscription_override
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_provider="anthropic",
        )
        cid = await _make_card(s, title="legacy-no-dispatch", column="analyst")
        # Global override pins every spawn to minimax.
        await set_active_subscription_override(
            s, PK, {"provider": "minimax", "model": None},
        )
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._limited_provider_for_card(
            s, project_key=PK, project_path="/p", card=card,
            target_column="analyst",
        )

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_limited_provider_for_card_falls_back_to_narrow_when_no_project_path():
    """Reaper path: a stuck session is cleaned up without project_path
    context (``reap_stale_claims`` is sometimes called with project_path=None
    in tests). When dispatch_provider is also None, the helper must NOT
    raise — it falls back to the narrow ``_provider_for_card`` so a
    best-effort answer is still available."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="bedrock",
        )
        cid = await _make_card(s, title="legacy-no-path", column="engineer")
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._limited_provider_for_card(
            s, project_key=PK, project_path=None, card=card,
            target_column="engineer",
        )

    assert resolved == "bedrock"


@pytest.mark.asyncio
async def test_limited_provider_for_card_returns_none_when_inputs_insufficient():
    """A bare None card / empty target_column short-circuits — same contract
    as ``_provider_for_card``. Caller falls back to the legacy global pause."""
    async with KanbanSessionLocal() as s:
        assert await dispatch._limited_provider_for_card(
            s, project_key=PK, project_path="/p", card=None,
            target_column="engineer",
        ) is None
        card = SimpleNamespace(dispatch_provider=None, column_overrides={},
                               model=None, agent="engineer")
        assert await dispatch._limited_provider_for_card(
            s, project_key=PK, project_path="/p", card=card,
            target_column="",
        ) is None


# ---- move_limited_session_to_resume honours dispatch_provider (kaart 9ff86416…)

@pytest.mark.asyncio
async def test_move_limited_session_uses_dispatch_provider_for_spillover(monkeypatch):
    """Card has dispatch_provider='minimax' in the analyst column (default
    anthropic). When the session hits its limit, the spillover check MUST be
    called with limited_provider='minimax' — not the column default — so the
    pool router marks the right subscription unavailable."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    spillover_calls: list[dict] = []

    async def _fake_spillover(session, *, project_key, limited_provider, cli_id, column=None):
        spillover_calls.append({
            "project_key": project_key,
            "limited_provider": limited_provider,
            "cli_id": cli_id,
            "column": column,
        })
        return True  # pretend minimax is available so we exercise the spillover path

    monkeypatch.setattr(dispatch, "_pool_spillover_available", _fake_spillover)

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_provider="anthropic",
        )
        cid = await _make_card(
            s, title="analyst-pool-429", column="analyst",
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_provider": "minimax"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-ana-0001"},
        )
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="anthropic", model=None, drempel=0.9),
            PoolEntry(provider="minimax", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-ana", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-ana-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    # Spillover branch fired, scheduled_at dropped for immediate redispatch.
    assert card.scheduled_at is None
    # The resolver called the spillover check with the card's actual
    # dispatch_provider, NOT the column default.
    assert len(spillover_calls) == 1
    assert spillover_calls[0]["limited_provider"] == "minimax"


@pytest.mark.asyncio
async def test_move_limited_session_legacy_card_keeps_column_default_behaviour(monkeypatch):
    """Legacy card (dispatch_provider=None) keeps the existing column-default
    resolution: the spillover check is called with the column's default
    provider. This pins today's behaviour so the fix doesn't regress legacy
    rows."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    spillover_calls: list[dict] = []

    async def _fake_spillover(session, *, project_key, limited_provider, cli_id, column=None):
        spillover_calls.append({"limited_provider": limited_provider, "column": column})
        return False  # legacy behaviour: stay on reset-time pause

    monkeypatch.setattr(dispatch, "_pool_spillover_available", _fake_spillover)

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="bedrock",
        )
        cid = await _make_card(
            s, title="legacy-429", column="engineer",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-leg-0001"},
        )
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="bedrock", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-leg", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-leg-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"
    assert len(spillover_calls) == 1
    assert spillover_calls[0]["limited_provider"] == "bedrock"


@pytest.mark.asyncio
async def test_provider_for_cwd_honours_dispatch_provider(monkeypatch):
    """Hook-event path: a Notification hook for a session that was actually
    running on minimax (dispatch_provider set) must return minimax even when
    the column default is anthropic. Pre-fix this returned the column default
    and the per-provider pause would gate anthropic instead of minimax."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_provider="anthropic",
        )
        cid = await _make_card(
            s, title="hook-minimax", column="analyst",
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_provider": "minimax"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-hook-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd(
            "/p/.claude/worktrees/k-hook-0001",
        )

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_cleanup_stuck_session_pauses_dispatch_provider(monkeypatch):
    """The reaper path must pause the subscription the session was running
    against, not the column default. dispatch_provider='minimax' with an
    anthropic column default MUST pause minimax."""
    import unittest.mock as mock

    from app.kanban import dispatch_pause

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic",
        )
        cid = await _make_card(
            s, title="stuck-dispatch-provider", column="engineer",
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_provider": "minimax"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stuck-0003"},
        )
        await s.commit()
        card = await get_card(s, cid)

    with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch._cleanup_stuck_session(
                s, card=card, project_key=PK,
                session_name="k-stuck-0003", pane_content="rate limited",
                project_path="/p",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        paused_minimax = await dispatch_pause.get_paused_until(
            s, provider="minimax"
        )
        paused_anthropic = await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        )

    assert paused_minimax is not None
    assert paused_anthropic is None


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_column_default_for_matching_session(monkeypatch):
    """Hook-event path: with cwd matching a worktree, _provider_for_cwd
    resolves (project, session, card) and returns the card's column default
    provider. Mirrors move_limited_session_to_resume's lookup so both paths
    agree on what counts as a 'matching' card."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="limax-card", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-prov-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd(
            "/p/.claude/worktrees/k-prov-0001",
        )

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_none_for_non_worktree_cwd(monkeypatch):
    """A cwd that isn't <project>/.claude/worktrees/<name> isn't ours to touch --
    same precondition move_limited_session_to_resume enforces, so the
    fallback path stays consistent."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd("/home/me/some-project")

    assert resolved is None


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_none_when_no_card_claims_session(monkeypatch):
    """No card claimed by that session -> None, the same condition under
    which move_limited_session_to_resume no-ops. The hook keeps the legacy
    global-pause behaviour in that case."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd(
            "/p/.claude/worktrees/k-prov-nonexistent",
        )

    assert resolved is None


@pytest.mark.asyncio
async def test_cleanup_stuck_session_pauses_only_affected_provider(monkeypatch):
    """The reaper path must mirror the hook: a stuck session running on a
    minimax column pauses only minimax. anthropic / bedrock stay clear so
    other traffic flows."""
    import unittest.mock as mock
    from datetime import UTC, datetime, timedelta

    from app.kanban import dispatch_pause
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="stuck-rl", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stuck-0001"},
        )
        await s.commit()
        card = await get_card(s, cid)

    # Keep tmux out of the picture: _kill_agent_session would otherwise hit the
    # host's tmux server (no such session here, returns None, harmless) but we
    # want a tight deterministic test.
    with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch._cleanup_stuck_session(
                s, card=card, project_key=PK,
                session_name="k-stuck-0001", pane_content="rate limited",
            )
            await s.commit()

    # Card's per-provider slot is set ...
    async with KanbanSessionLocal() as s:
        paused_minimax = await dispatch_pause.get_paused_until(
            s, provider="minimax"
        )
        paused_minimax_active = await dispatch_pause.is_dispatch_paused(
            s, provider="minimax"
        )
        # ... legacy global slot is NOT touched ...
        paused_global = await dispatch_pause.get_paused_until(s)
        paused_global_active = await dispatch_pause.is_dispatch_paused(s)
        # ... and sibling providers stay clear.
        paused_anthropic = await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        )
        paused_bedrock = await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        )

    expected_deadline = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
    assert paused_minimax is not None
    assert paused_minimax_active is True
    assert abs((paused_minimax - expected_deadline).total_seconds()) < 30
    assert paused_global is None
    assert paused_global_active is False
    assert paused_anthropic is None
    assert paused_bedrock is None


@pytest.mark.asyncio
async def test_cleanup_stuck_session_pauses_provider_from_column_override(monkeypatch):
    """When the card carries a per-column provider override, the pause targets
    THAT provider (bedrock here), not the column default -- a stale override
    on a stale card would otherwise pause the wrong subscription."""
    import unittest.mock as mock

    from app.kanban import dispatch_pause

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="stuck-override", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stuck-0002"},
        )
        await s.commit()
        card = await get_card(s, cid)

    with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch._cleanup_stuck_session(
                s, card=card, project_key=PK,
                session_name="k-stuck-0002", pane_content="rate limited",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s, provider="bedrock") is True
        assert await dispatch_pause.is_dispatch_paused(s, provider="minimax") is False
        assert await dispatch_pause.is_dispatch_paused(s) is False


# ---- set_resume race guard ([self-improve] set_resume races a fresh auto-dispatch) ----
#
# Symptom: an operator calls `mcp_server.set_resume(card_id, session_id, ...)`
# to mark a card for resume mode (claude --resume) on the next dispatch.
# Within milliseconds, the auto-dispatch tick fires and dispatches the card
# with the **worktree** transport, spawning a brand-new worktree + session
# — exactly defeating the operator's intent. Root cause: the tick's cached
# `cards` list reflects state *before* `set_resume` committed, AND the
# reaper's `_release_dead_claim` then strips `resume_session_id` so even
# a re-read on the next iteration sees no resume pointer.
#
# The fix has three parts, all observable from this block:
#   1. `mcp_server.set_resume` stamps `scheduled_at = now + RESUME_RACE_GUARD_S`
#      so the same dispatch-sweep pass defers the card via the existing
#      `_is_due` gate.
#   2. `dispatch._run_card` re-reads the card from the DB before claiming,
#      catching both the fresh `resume_session_id` (concurrent set_resume)
#      and the fresh `scheduled_at` (concurrent defer).
#   3. `dispatch._release_dead_claim` no longer calls `_clear_stale_resume_fields`
#      for the not-dead-on-arrival branch — a long-running session that died
#      cleanly must keep the operator's just-stamped `resume_session_id`.

from datetime import UTC as _UTC  # noqa: F401
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta


@pytest.mark.asyncio
async def test_set_resume_mcp_stamps_scheduled_at_to_hold_off_dispatch():
    """Setting resume_session_id via the MCP tool also stamps a near-future
    scheduled_at so the card is held out of the *same* dispatch-sweep pass
    that races the write. Without this guard, an in-flight dispatch tick
    whose cards-list read predates set_resume will pick the card up and
    dispatch it with the worktree transport — see kanban card
    `[self-improve] set_resume races a fresh auto-dispatch`."""
    from app.kanban import mcp_server as m

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resume-race-guard", column="Backlog")
        await s.commit()

    before = _datetime.now(_UTC)
    await m.set_resume(cid, "sess-explicit", project_folder="proj-folder")
    after = _datetime.now(_UTC)

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert card.resume_session_id == "sess-explicit"
    assert card.resume_project_folder == "proj-folder"
    assert card.scheduled_at is not None
    fire_at = _datetime.fromisoformat(card.scheduled_at)
    # Guard window must be near-future (small, not "next hour" — that's the
    # reaper's fallback path, which is a different code path). Pick something
    # conservative so the test fails loud if the constant explodes.
    assert fire_at > before
    assert fire_at <= after + _timedelta(seconds=10)


@pytest.mark.asyncio
async def test_set_resume_does_not_overwrite_existing_future_scheduled_at():
    """If the card already carries a future scheduled_at (e.g. a reaper
    fallback set it to "next hour" because no resumable worktree was found),
    set_resume must not clobber it — that schedule is intentional and the
    operator's resume stamp should layer on top without re-scheduling."""
    from app.kanban import mcp_server as m

    far_future = (_datetime.now(_UTC) + _timedelta(hours=2)).isoformat()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="preserve-existing", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"scheduled_at": far_future},
        )
        await s.commit()

    await m.set_resume(cid, "sess-late", project_folder="proj-folder")

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert card.resume_session_id == "sess-late"
    assert card.scheduled_at == far_future  # preserved, not overwritten


@pytest.mark.asyncio
async def test_run_card_re_reads_to_pick_up_concurrent_set_resume():
    """Regression test for the cached-card race: dispatch_project reads its
    `cards` list at the top of the tick. A `set_resume` MCP call that
    commits *between* that read and `_run_card`'s claim would otherwise be
    masked by the stale card object in `dispatch_project`'s working set,
    and the dispatch would pick the worktree transport — defeating the
    operator's intent.

    With the fix, `_run_card` re-reads the card from the DB right before
    claiming, so a just-set `resume_session_id` wins and the resume
    transport is selected."""
    import unittest.mock as mock

    # Card created on Backlog, no claim — the cached stale_card has no
    # resume_session_id. The DB has the resume_session_id set (simulating
    # a set_resume that landed between the dispatch tick's cards-list read
    # and _run_card).
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="race-card", column="Backlog")
        await s.commit()
        stale_card = await get_card(s, cid)

    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"resume_session_id": "sess-fresh",
                     "resume_project_folder": "proj-folder"},
        )
        await s.commit()

    resume_calls: list[str] = []
    worktree_calls: list[str] = []

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code",
                         provider="anthropic", model=None, **kwargs):
        resume_calls.append(session_name)
        return {"session_name": session_name}

    def worktree_transport(*, directory, prompt, session_name, cli_id="claude-code",
                            provider="anthropic", model=None, **kwargs):
        worktree_calls.append(session_name)
        return {"session_name": session_name}

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            await dispatch._run_card(
                s, card=stale_card, project_key=PK, project_path="/p",
                transport=worktree_transport, live_sessions=set(),
            )
            await s.commit()

    # Without the re-read fix: worktree_transport was used (1 call), the
    # operator's set_resume is silently ignored. With the fix: the re-read
    # sees resume_session_id, make_resume_transport is consulted, and the
    # resume transport is the one called.
    assert len(resume_calls) == 1, (
        f"expected 1 resume transport call, got {len(resume_calls)} "
        f"(worktree_calls={len(worktree_calls)})"
    )
    assert len(worktree_calls) == 0


@pytest.mark.asyncio
async def test_run_card_defers_to_next_tick_when_fresh_scheduled_at_is_future():
    """If the re-read card has a future `scheduled_at` (e.g. set_resume's
    race-guard deferred it to the next tick), `_run_card` must bail rather
    than claim + spawn — the operator-set hold-out is honored even when the
    dispatch tick is already mid-flight."""
    import unittest.mock as mock

    # Step 1: create the card and read it BEFORE stamping the guard, so the
    # cached card really is stale (no scheduled_at, no resume_session_id).
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="defer-this-card", column="Backlog")
        await s.commit()
        stale_card = await get_card(s, cid)

    # Step 2: now write the resume fields + the race-guard scheduled_at in
    # the DB. The cached `stale_card` still has neither.
    future = (_datetime.now(_UTC) + _timedelta(seconds=30)).isoformat()
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"resume_session_id": "sess-fresh",
                     "resume_project_folder": "proj-folder",
                     "scheduled_at": future},
        )
        await s.commit()

    # Sanity check: the cached card really doesn't carry the guard.
    assert stale_card.scheduled_at is None
    assert stale_card.resume_session_id is None

    transport = RecordingTransport()
    with mock.patch.object(dispatch, "make_resume_transport",
                            return_value=lambda **_: {"session_name": "noop"}):
        async with KanbanSessionLocal() as s:
            result = await dispatch._run_card(
                s, card=stale_card, project_key=PK, project_path="/p",
                transport=transport, live_sessions=set(),
            )
            await s.commit()
            card = await get_card(s, cid)

    # _run_card must bail — no claim, no transport call, no telemetry write.
    assert result is None
    assert card.claimed_by is None, (
        f"expected no claim while scheduled_at is future, got claimed_by={card.claimed_by!r}"
    )
    assert len(transport.calls) == 0


@pytest.mark.asyncio
async def test_reaper_release_dead_claim_preserves_operator_set_resume():
    """A long-running session (claim age >> DEAD_ON_ARRIVAL_SECONDS) that
    died cleanly must NOT have its operator-stamped `resume_session_id`
    cleared by the reaper. Without this, `set_resume` followed by an
    immediate reaper pass leaves the card without the resume pointer, so
    the next dispatch uses the worktree transport — the very bug the
    `[self-improve] set_resume races a fresh auto-dispatch` card names."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    # Card on engineer with a dead agent: claim — simulate the long-running
    # session that finished its work, merged, then died.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="dead-but-resumable", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-old"},
        )
        # Operator stamped a fresh resume_session_id via set_resume AFTER
        # the session died. Worktree is gone (merge + GC), so _move_to_resume
        # returns False and the reaper falls through to plain release.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"resume_session_id": "sess-operator-stamped",
                     "resume_project_folder": "proj-folder"},
        )
        await s.commit()

    # Forge the claim as "old" so dead_on_arrival is False. Direct ORM
    # assignment — apply_operation's `update` payload doesn't carry
    # `claimed_at`, so the LWW plumbing would silently drop it.
    async with KanbanSessionLocal() as s:
        await _backdate_claim(s, cid, seconds_ago=2 * 3600)
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=None,
    ):
        async with KanbanSessionLocal() as s:
            reaped = await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions=set(),
                # project_path=None forces the plain-release fallback path.
            )
            await s.commit()
            card = await get_card(s, cid)

    assert reaped == 1
    assert card.claimed_by is None  # claim released
    # Operator's resume_session_id must survive — this is the whole point.
    assert card.resume_session_id == "sess-operator-stamped", (
        "reaper stripped the operator's resume_session_id; the next dispatch "
        "will use the worktree transport, exactly the bug this card fixes"
    )


@pytest.mark.asyncio
async def test_set_resume_then_immediate_dispatch_defers_to_next_tick():
    """End-to-end race acceptance: calling set_resume on a card with a dead
    `agent:` claim, then calling `dispatch_project` in the same pass, must
    NOT spawn a new worktree + session. The next pass (after the race guard
    expires) is the one that picks it up — and it must use the resume
    transport.

    This is the integration test for `[self-improve] set_resume races a
    fresh auto-dispatch`. Without the fix, the same dispatch_project call
    claims the card with a fresh `agent:` session name and uses the worktree
    transport (the bug). With the fix, the same call only reaps the dead
    claim; the card stays unclaimed until the guard expires."""
    import unittest.mock as mock

    from app.kanban import mcp_server as m
    from app.kanban import session_recovery

    # Card on engineer, claimed by a long-dead session whose worktree is
    # gone (worktree path returns None from _resolve_resume_target).
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="race-card", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-zzzz"},
        )
        await s.commit()

    # Forge the claim as "old" so dead_on_arrival is False (the branch that
    # preserves resume_session_id). Direct ORM — apply_operation's update
    # payload doesn't carry claimed_at, so the LWW plumbing would silently
    # drop a payload-based claim_at write.
    async with KanbanSessionLocal() as s:
        await _backdate_claim(s, cid, seconds_ago=2 * 3600)
        await s.commit()

    # Operator calls set_resume — this is the MCP entry point that the user
    # clicks "Continue from where you left off" on.
    await m.set_resume(cid, "sess-explicit", project_folder="proj-folder")

    # In the same pass, the auto-dispatch tick fires. We simulate it.
    resume_calls: list[str] = []
    worktree_calls: list[str] = []

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code",
                         provider="anthropic", model=None, **kwargs):
        resume_calls.append(session_name)
        return {"session_name": session_name}

    def worktree_transport(*, directory, prompt, session_name, cli_id="claude-code",
                            provider="anthropic", model=None, **kwargs):
        worktree_calls.append(session_name)
        return {"session_name": session_name}

    with mock.patch.object(session_recovery, "_resolve_resume_target",
                            return_value=None), \
         mock.patch.object(dispatch, "make_resume_transport",
                            return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p",
                transport=worktree_transport, live_sessions=set(),
            )
            await s.commit()
            card_after_same_pass = await get_card(s, cid)

    # Same pass: no fresh worktree (the bug would create one) and no claim
    # by a different agent id. The reaper released the dead claim, but the
    # operator's `scheduled_at` guard defers any re-claim to the next tick.
    assert len(worktree_calls) == 0, (
        "same-pass dispatch created a worktree — the resume-race fix is broken"
    )
    assert card_after_same_pass.claimed_by is None, (
        f"expected no fresh claim in same pass, got {card_after_same_pass.claimed_by!r}"
    )
    assert card_after_same_pass.resume_session_id == "sess-explicit"

    # Now simulate the guard expiring (next tick). Manually clear scheduled_at
    # so _is_due returns True and the dispatch picks the card up.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"scheduled_at": None},
        )
        await s.commit()

    resume_calls.clear()
    worktree_calls.clear()
    with mock.patch.object(session_recovery, "_resolve_resume_target",
                            return_value=None), \
         mock.patch.object(dispatch, "make_resume_transport",
                            return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            next_pass = await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p",
                transport=worktree_transport, live_sessions=set(),
            )
            await s.commit()
            card_after_next_pass = await get_card(s, cid)

    # Next pass: the resume transport is selected (the operator's intent is
    # honored). The worktree transport is NOT used.
    assert next_pass is not None
    assert len(resume_calls) == 1
    assert len(worktree_calls) == 0
    assert card_after_next_pass.claimed_by is not None
    assert card_after_next_pass.claimed_by.startswith("agent:")


# ---- re-dispatch prompt: prior-branch warning (kaart ff2d03fce…) ----------
#
# Acceptance criteria (from the card):
#   [1] re-dispatch with prior branch ahead of origin/master → warning
#       rendered with branch name + commit count
#   [2] first dispatch (no prior branch) → no warning
#   [3] test covers both paths
#
# Two layers, each with its own pure helper:
#   1. `_build_prior_branch_warning(project_path, prior_session_name)` runs
#      `git log origin/master..<branch> --oneline` against the project repo
#      (NOT the worktree — the worktree may already be GC'd) and renders a
#      warning block when the output is non-empty. Returns "" otherwise.
#   2. `build_card_prompt` accepts an optional `prior_branch_warning` arg and
#      prepends the warning near the top of the prompt when non-empty, so a
#      re-dispatched agent sees it in early context (same parity principle as
#      the worktree-safety callout).
#
# The git checks run synchronously against a tmp_path repo with a real
# `origin/master` and a feature branch; this keeps the test hermetic and
# avoids depending on kanban DB state.

def _tmp_repo_with_master(tmp_path):
    """Init a tmp git repo with a 'master' branch on a fake origin — shared
    by both prior-branch test classes so each test doesn't pay the setup cost."""
    proj = tmp_path / "proj"
    proj.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        **os.environ,
    }
    def run(*args, **kwargs):
        return subprocess.run(
            list(args), capture_output=True, text=True, env=env,
            cwd=str(proj), **kwargs,
        )
    run("git", "init", "-q", "-b", "master", ".")
    run("git", "config", "user.email", "t@x")
    run("git", "config", "user.name", "T")
    (proj / "README").write_text("hi\n")
    run("git", "add", "README")
    run("git", "commit", "-q", "-m", "initial")
    origin = tmp_path / "origin.git"
    origin.mkdir()
    run("git", "init", "-q", "--bare", str(origin))
    run("git", "remote", "add", "origin", str(origin))
    run("git", "push", "-q", "origin", "master")
    return proj


class TestBuildPriorBranchWarning:
    """Pure helper that surfaces "a prior dispatch left commits behind" warnings."""

    def test_returns_empty_when_no_prior_session(self, tmp_path):
        proj = _tmp_repo_with_master(tmp_path)
        # No prior session name → no warning (the empty string is the explicit
        # "no warning" sentinel — callers prepend it only when non-empty).
        assert dispatch._build_prior_branch_warning(str(proj), None) == ""
        assert dispatch._build_prior_branch_warning(str(proj), "") == ""

    def test_returns_empty_when_prior_branch_has_no_unmerged_commits(self, tmp_path):
        proj = _tmp_repo_with_master(tmp_path)
        env = {
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            **__import__("os").environ,
        }
        def run(*args):
            return subprocess.run(
                list(args), capture_output=True, text=True, env=env, cwd=str(proj),
            )
        # A branch with NO new commits → nothing to warn about.
        run("git", "checkout", "-q", "-b", "k-prior-clean-abcd")
        assert (
            dispatch._build_prior_branch_warning(str(proj), "k-prior-clean-abcd") == ""
        )

    def test_returns_warning_when_prior_branch_ahead_of_master(self, tmp_path):
        proj = _tmp_repo_with_master(tmp_path)
        env = {
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            **__import__("os").environ,
        }
        def run(*args):
            return subprocess.run(
                list(args), capture_output=True, text=True, env=env, cwd=str(proj),
            )
        # A branch with 2 commits ahead of origin/master → warning expected.
        run("git", "checkout", "-q", "-b", "k-prior-dirty-abcd")
        (proj / "A").write_text("a\n")
        run("git", "add", "A")
        run("git", "commit", "-q", "-m", "add A")
        (proj / "B").write_text("b\n")
        run("git", "add", "B")
        run("git", "commit", "-q", "-m", "add B")
        out = dispatch._build_prior_branch_warning(str(proj), "k-prior-dirty-abcd")
        # Branch name + commit count are explicit acceptance criteria.
        assert "k-prior-dirty-abcd" in out
        assert "2" in out
        # Heading + a usable inspection hint must both be present.
        assert "Let op" in out or "WAARSCHUWING" in out.upper() or "warning" in out.lower()
        assert "git log" in out

    def test_returns_empty_when_prior_branch_does_not_exist(self, tmp_path):
        # Branch was force-deleted or never pushed → no warning, no crash.
        proj = _tmp_repo_with_master(tmp_path)
        assert (
            dispatch._build_prior_branch_warning(str(proj), "k-never-existed-xxxx") == ""
        )

    def test_returns_empty_when_repo_unavailable(self, tmp_path):
        # Malformed project path → fail open (empty), never raise — a transient
        # git hiccup must not wedge dispatch.
        assert (
            dispatch._build_prior_branch_warning("/nonexistent/repo", "k-x-yz01") == ""
        )


class TestBuildCardPromptPriorBranchWarning:
    """build_card_prompt must surface the prior-branch warning at the top of
    the prompt when one is supplied, and stay silent when it isn't (the
    'no ruis in de normale prompt' acceptance criterion)."""

    def _card(self):
        class _C:
            title = "T"
            description = "do thing"
        return _C()

    def test_warning_prepended_when_supplied(self):
        warning = (
            "**Let op:** een eerdere sessie (`k-prior-abcd`) liet 2 commits "
            "achter die nog niet gemerged zijn."
        )
        prompt = dispatch.build_card_prompt(
            self._card(), persona=None, ship_mode="direct",
            prior_branch_warning=warning,
        )
        # Acceptance criterion: warning present with branch + count.
        assert "k-prior-abcd" in prompt
        assert "2 commits" in prompt
        # The warning must land BEFORE the ship workflow so the agent sees it
        # in early context (same parity as the worktree-safety callout).
        assert prompt.index("k-prior-abcd") < prompt.index("Session-end workflow")

    def test_no_warning_section_when_none(self):
        prompt = dispatch.build_card_prompt(
            self._card(), persona=None, ship_mode="direct",
            prior_branch_warning=None,
        )
        # Acceptance criterion: no ruis — first dispatch keeps the legacy shape.
        assert "Let op" not in prompt
        assert "eeerdere sessie" not in prompt
        assert "git log origin/master..<branch>" not in prompt

    def test_warning_section_renders_inside_a_named_heading(self, tmp_path):
        """The end-to-end shape: when the helper renders a warning for a real
        prior branch, that warning sits inside a markdown `##`-style heading
        so a human skimming the prompt recognises it at a glance, and future
        prompt tests can pin the exact substring without coupling to the
        prose. This exercises the dispatcher wiring (helper → builder →
        prompt), not the builder alone — the builder is a dumb renderer, the
        heading comes from the helper."""
        # Build a tmp repo with a prior branch that is actually ahead of
        # origin/master (one commit pushed to the local branch, none on the
        # remote) so `_build_prior_branch_warning` produces real output —
        # the helper only renders a warning when there are commits to surface.
        proj = _tmp_repo_with_master(tmp_path)
        env = {
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
        }
        def run(*args):
            return subprocess.run(
                list(args), capture_output=True, text=True, env=env, cwd=str(proj),
            )
        run("git", "checkout", "-q", "-b", "k-prior-abcd")
        (proj / "prior.txt").write_text("prior\n")
        run("git", "add", "prior.txt")
        run("git", "commit", "-q", "-m", "prior commit")
        warning = dispatch._build_prior_branch_warning(str(proj), "k-prior-abcd")
        assert "##" in warning, (
            f"helper output must contain a markdown heading; got {warning!r}"
        )
        # Now verify the rendered warning reaches the prompt intact, inside
        # its heading.
        prompt = dispatch.build_card_prompt(
            self._card(), persona=None, ship_mode="direct",
            prior_branch_warning=warning,
        )
        # Both the heading and the body land in the prompt.
        assert "##" in prompt
        assert warning.strip().splitlines()[0] in prompt


class TestResolvePriorBranchWarning:
    """End-to-end: the dispatcher injects a prior-branch warning when (and
    only when) the card has a prior `agent:` claim and that prior branch is
    ahead of origin/master. This is the actual acceptance criterion from
    kaart ff2d03fce… — the helper is wired into `_run_card`, and the wiring
    reaches every dispatch entry point (auto-tick, manual dispatch_card,
    redispatch_card, dispatch_impediment_card).

    Uses a real RecordingTransport so we can assert on the prompt that
    reaches the spawned session, not just intermediate state."""

    async def test_first_dispatch_has_no_warning(self, tmp_path):
        """A card that was never claimed before → no prior-branch warning in
        the spawned prompt. 'Geen ruis in de normale prompt' (AC #2)."""
        proj = _tmp_repo_with_master(tmp_path)
        transport = RecordingTransport()
        async with KanbanSessionLocal() as s:
            cid = await _make_card(s, title="fresh card")
            await s.commit()
            await dispatch.dispatch_card(
                s, card_id=cid, project_path=str(proj),
                transport=transport,
            )
        assert len(transport.calls) == 1
        prompt = transport.calls[0]["prompt"]
        assert "PRID-BRANCH-WAARSCHUWING" not in prompt
        assert "Let op" not in prompt or "Let op:" not in prompt.split("Let op")[0] + "x"

    async def test_redispatch_with_unmerged_commits_injects_warning(self, tmp_path):
        """A card that was previously claimed by a session whose branch is
        ahead of origin/master → the new spawned session sees the
        `## PRID-BRANCH-WAARSCHUWING` block with branch + commit count.
        (AC #1)."""
        proj = _tmp_repo_with_master(tmp_path)
        env = {
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
        }
        def run(*args):
            return subprocess.run(
                list(args), capture_output=True, text=True, env=env, cwd=str(proj),
            )
        # Simulate the prior session: create the worktree-style branch on the
        # project repo, push 2 commits ahead of origin/master. The branch
        # name matches what the dispatcher would have minted (k-<slug>-<4hex>)
        # — we don't need to match exactly, only need it to look like one.
        prior_branch = "k-prior-card-abcd"
        run("git", "checkout", "-q", "-b", prior_branch)
        (proj / "p1.txt").write_text("p1\n")
        run("git", "add", "p1.txt")
        run("git", "commit", "-q", "-m", "p1")
        (proj / "p2.txt").write_text("p2\n")
        run("git", "add", "p2.txt")
        run("git", "commit", "-q", "-m", "p2")

        transport = RecordingTransport()
        async with KanbanSessionLocal() as s:
            cid = await _make_card(s, title="redisp")
            # Stage the op-log to look like a prior claim (and release) of
            # this same session name. This is the trace the reaper leaves
            # behind when a session dies between commit and merge.
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": f"agent:{prior_branch}"},
            )
            await apply_operation(
                s, op_type="release", entity_type="card", project_key=PK,
                entity_id=cid, payload={},
            )
            await s.commit()
            await dispatch.dispatch_card(
                s, card_id=cid, project_path=str(proj),
                transport=transport,
            )
        assert len(transport.calls) == 1
        prompt = transport.calls[0]["prompt"]
        # Branch name + commit count must be in the prompt.
        assert prior_branch in prompt
        assert "2" in prompt
        assert "PRID-BRANCH-WAARSCHUWING" in prompt

    async def test_redispatch_with_merged_branch_emits_no_warning(self, tmp_path):
        """A prior branch that has zero commits ahead of origin/master (e.g.
        already merged in a concurrent merge) → no warning; the helper
        correctly identifies 'nothing to surface' and stays silent. This is
        the third path the acceptance criteria cover."""
        proj = _tmp_repo_with_master(tmp_path)
        env = {
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x",
            "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
        }
        def run(*args):
            return subprocess.run(
                list(args), capture_output=True, text=True, env=env, cwd=str(proj),
            )
        # Branch with no new commits (no checkout, just create and leave).
        merged_branch = "k-already-merged-1234"
        run("git", "checkout", "-q", "-b", merged_branch)
        run("git", "checkout", "-q", "master")

        transport = RecordingTransport()
        async with KanbanSessionLocal() as s:
            cid = await _make_card(s, title="merged")
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": f"agent:{merged_branch}"},
            )
            await apply_operation(
                s, op_type="release", entity_type="card", project_key=PK,
                entity_id=cid, payload={},
            )
            await s.commit()
            await dispatch.dispatch_card(
                s, card_id=cid, project_path=str(proj),
                transport=transport,
            )
        assert len(transport.calls) == 1
        prompt = transport.calls[0]["prompt"]
        assert "PRID-BRANCH-WAARSCHUWING" not in prompt



@pytest.mark.asyncio
async def test_manual_pause_gate_skips_provider_resolution_when_no_manual_pause(monkeypatch):
    async with KanbanSessionLocal() as s:
        card = SimpleNamespace(column_overrides={}, model=None, agent="engineer")

        async def unexpected_resolution(*args, **kwargs):
            raise AssertionError("provider resolution should be skipped")

        from app.kanban import dispatch_pause

        async def no_manual_pauses(session):
            return []

        monkeypatch.setattr(dispatch, "_effective_provider_for_pause_gate", unexpected_resolution)
        monkeypatch.setattr(dispatch_pause, "list_manually_paused_providers", no_manual_pauses)
        assert await dispatch._card_is_manually_paused(
            s, project_key=PK, project_path="/tmp/project", card=card,
            target_column="engineer",
        ) is False


# ---- card 572af2d6: review-card hold --------------------------------------
#
# Bug: an original card moved back to Backlog while a review-card sibling
# (metadata.reviewed_card_id == original.id) was still open got re-dispatched
# within 98 seconds, spawning an Opus analysis session that duplicated the work
# the review had already filed as child cards. Auto-dispatch read the
# **Review requested:** comment but no signal reached its gates.
#
# Fix: while a card has an open review-card sibling, dispatch must hold it out
# the same way ``awaiting_plan_ref`` holds child cards — visibly
# (held_reason/held_blocker) and only auto-clearing when the review card reaches
# a terminal column (Done/Impediment).


async def _make_review_card(s, *, original_id, title="Review: original",
                           column="Backlog", priority="high"):
    """Create a sibling card pointing back at ``original_id`` via the
    ``metadata.reviewed_card_id`` link that ``request_review`` plants.

    Mirrors the service-level ``request_review`` shape (Backlog column,
    work_type='analysis', priority='high') so the resulting hold is
    exercised against the same board topology the production flow produces.
    """
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload={"title": title, "column": column,
                 "work_type": "analysis", "agent": "analyst",
                 "priority": priority,
                 "metadata": {"reviewed_card_id": original_id}},
    )
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_next_card_skips_origin_with_open_review_card():
    """Bug card 572af2d6 acceptance (1) — negative side:
    ``_next_card`` must NOT pick an origin card that has an open review-card
    sibling (metadata.reviewed_card_id == origin.id), even though the origin is
    in Backlog and otherwise dispatchable.

    Both cards have priority='high' so the priority sort is a no-op (stable
    sort preserves input order); the only thing distinguishing them for
    ``_next_card`` is therefore the new hold. Without the fix the origin wins
    on input order and is dispatched — the exact bug 572af2d6 describes.
    """
    async with KanbanSessionLocal() as s:
        origin = await _make_card(
            s, title="reviewed original", column="Backlog", priority="high",
        )
        review = await _make_review_card(s, original_id=origin, priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        origin_card = next(c for c in cards if c.id == origin)
        review_card = next(c for c in cards if c.id == review)
        # Input order: origin first. Without the hold, stable priority sort
        # returns the origin; with the hold, origin is skipped and review wins.
        picked = dispatch._next_card([origin_card, review_card])

    assert picked is not None, (
        "_next_card returned None — the review card should still be dispatchable "
        "on its own merits even though the origin is held"
    )
    assert picked.id != origin, (
        f"origin {origin} must be held while review {review} is open; "
        f"_next_card returned {picked.id} (priority sort alone would have picked "
        "the origin without the new hold — that's the bug)"
    )
    assert picked.id == review


@pytest.mark.asyncio
async def test_origin_with_review_card_moved_to_done_is_dispatched():
    """Bug card 572af2d6 acceptance (1) — positive side:
    once the review card reaches Done, the hold clears and the origin becomes
    a normal Backlog card again. ``_next_card`` picks it up.

    This is the half the bug card explicitly demands: a test that only asserts
    'is not picked while held' is green whenever ``_next_card`` returns None
    for ANY reason, so the positive side is what makes the test sharp.
    """
    async with KanbanSessionLocal() as s:
        origin = await _make_card(s, title="reviewed original", column="Backlog")
        review = await _make_review_card(s, original_id=origin)
        await apply_operation(
            s, op_type="move", entity_type="card", project_key="",
            entity_id=review, payload={"column": "Done"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        origin_card = next(c for c in cards if c.id == origin)
        picked = dispatch._next_card([origin_card])

    assert picked is not None, (
        "origin must be picked after review card is Done; the positive side of "
        "the hold test is what proves the gate actually clears (not just that "
        "it fires)"
    )
    assert picked.id == origin


@pytest.mark.asyncio
async def test_origin_held_reason_records_open_review_card_id():
    """Acceptance (2) — visibility: the hold must surface ``held_reason`` and
    ``held_blocker`` on the origin so the board shows *why* it is sitting still
    (silent holds were the trap that ``awaiting_plan_ref`` already walked
    into). The blocker's value is the review card's id so a UI can deep-link
    straight to the sibling.
    """
    async with KanbanSessionLocal() as s:
        origin = await _make_card(s, title="reviewed original", column="Backlog")
        review = await _make_review_card(s, original_id=origin)
        await s.commit()
        # Run the dispatch tick so _persist_holds stamps held_reason/held_blocker.
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p",
            transport=RecordingTransport(),
        )
        await s.commit()
        origin_card = await get_card(s, origin)

    assert origin_card.held_reason == "awaiting_review", (
        f"expected held_reason='awaiting_review', got {origin_card.held_reason!r}"
    )
    assert origin_card.held_blocker == [review], (
        f"expected held_blocker=[{review}], got {origin_card.held_blocker!r}"
    )


@pytest.mark.asyncio
async def test_origin_spawned_after_review_card_done():
    """End-to-end (acceptance 1+3): ``dispatch_project`` does not spawn the
    origin card while its review sibling is open, and does spawn it once the
    review card reaches Done — without a manual hold-clearing step.

    The review card itself IS a normal dispatchable card (work_type='analysis',
    priority='high'), so it gets spawned on the first tick — that's the
    review-flow doing its job. The bug 572af2d6 describes is specifically the
    ORIGIN being re-spawned while the review is in flight; this test pins
    that down by asserting the spawn's card_id is the review card, not the
    origin, on tick 1, and that tick 2 lands on the origin after the review
    reaches Done.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        origin = await _make_card(s, title="reviewed original", column="Backlog")
        review = await _make_review_card(s, original_id=origin)
        await s.commit()

        # Tick 1: review is open in Backlog → origin must NOT be claimed.
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        origin_card = await get_card(s, origin)
    assert origin_card.column == "Backlog"
    assert not origin_card.claimed_by, (
        f"origin must NOT be claimed while review {review} is open"
    )
    # The review card is allowed to spawn — what's NOT allowed is the origin
    # being picked while its review sibling is still running.
    spawned_ids = [c["card_id"] for c in transport.calls]
    assert origin not in spawned_ids, (
        f"origin {origin} was spawned while review {review} was open: "
        f"{transport.calls!r}"
    )

    # Tick 2: review moves to Done → origin becomes dispatchable.
    transport2 = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="move", entity_type="card", project_key="",
            entity_id=review, payload={"column": "Done"},
        )
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport2,
        )
        await s.commit()
        origin_card = await get_card(s, origin)
    spawned_ids_2 = [c["card_id"] for c in transport2.calls]
    assert origin in spawned_ids_2, (
        f"origin must be spawned after review Done; calls={transport2.calls!r}"
    )
    assert origin_card.claimed_by
