"""Behavioural test for the ship-recipe carve-out: subset + marker boundary.

The carve-out must accept a *non-empty subset* of
``{docs/cockpit/README.md, docs/cockpit/llms.txt}`` and, for ``README.md``
conflicts, verify that all hunks lie between the ``BEGIN/END GENERATED DOC
INDEX`` markers. Conflicts in non-generated files, or ``README.md``
conflicts outside the markers, must fall through to ``report_impediment``
(exit 1).

Pins the carve-out change from kanban card
``72db7429e0704664b59b262174312d80``: the previous
``[ "$CONFLICTED" != "$EXPECTED" ]`` strict-equality check rejected a
subset (e.g. a conflict in only ``README.md``) and forced a
``report_impediment`` fallback for what was actually a regenerable
generated-file conflict.

The test extracts the carve-out bash block from each mirror (the
``git-ship`` skill and the ``_build_ship_instructions`` inline mirror),
runs it against controlled fixtures, and asserts the right behaviour.
The structural (substring-presence) counterpart of these checks lives
in ``test_ship_recipe_drift.py``.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.kanban import dispatch

REPO_ROOT = Path(__file__).resolve().parents[2]

# The markers delimiting the auto-generated block in docs/cockpit/README.md.
# Mirrors `scripts/generate-doc-index.py:BEGIN_MARKER` / `END_MARKER`. The
# strings here are deliberately *prefixes* of the full HTML-comment markers
# so the test can grep for them with `grep -nF` without quoting the
# em-dash / parentheses that the upstream markers carry.
BEGIN_MARKER_LINE = "<!-- BEGIN GENERATED DOC INDEX"
END_MARKER_LINE = "<!-- END GENERATED DOC INDEX -->"


def _dispatch_direct_prompt() -> str:
    """Render the direct-mode ship instructions as the agent would see them."""
    return dispatch._build_ship_instructions("direct")


def _skill_md_direct_recipe() -> str:
    """Read the full git-ship skill; §4a is the direct-mode recipe."""
    return (REPO_ROOT / ".claude/skills/git-ship/SKILL.md").read_text(encoding="utf-8")


SOURCES: dict[str, callable[[], str]] = {
    "dispatch._build_ship_instructions('direct')": _dispatch_direct_prompt,
    ".claude/skills/git-ship/SKILL.md": _skill_md_direct_recipe,
}


# Pattern that matches the carve-out bash block: from the merge-failed
# ``if ! git -C "$WT" merge --no-ff`` opener to the closing
# ``commit --no-edit``. Both mirrors carry the same shape; this regex
# captures the carve-out as a standalone script we can splice into a
# test runner.
_MERGE_OPENER = 'if ! git -C "$WT" merge --no-ff'
_MERGE_CLOSER = 'git -C "$WT" commit --no-edit'


def _extract_carve_out_block(source_text: str) -> str:
    """Return the carve-out bash block (merge-failed branch) as a string.

    The carve-out lives between the merge-failed ``if ! ... ; then`` line
    and the closing ``fi`` (the line after ``commit --no-edit``). Extract
    verbatim — preserving the exact bash the agent would execute — so a
    refactor that re-indents, adds a sub-shell, or wraps a function
    still surfaces as a behavioural difference in the tests below.

    The closing ``fi`` matters: without it bash sees an unterminated
    ``if`` and aborts with ``syntax error: unexpected end of file``,
    which is what every assertion in this file would then trip on.
    """
    start_idx = source_text.find(_MERGE_OPENER)
    if start_idx == -1:
        raise AssertionError(
            f"could not find merge-failed opener ({_MERGE_OPENER!r}) in source"
        )
    closer_idx = source_text.find(_MERGE_CLOSER, start_idx)
    if closer_idx == -1:
        raise AssertionError(
            f"could not find carve-out closer ({_MERGE_CLOSER!r}) in source"
        )
    # Walk past the closer line so the matching ``fi`` is included.
    end_of_closer_line = source_text.find("\n", closer_idx)
    if end_of_closer_line == -1:
        raise AssertionError(
            "could not find newline after carve-out closer in source"
        )
    # The ``fi`` is the next non-empty, non-comment line after the closer.
    cursor = end_of_closer_line + 1
    while cursor < len(source_text):
        line_end = source_text.find("\n", cursor)
        if line_end == -1:
            line_end = len(source_text)
        line = source_text[cursor:line_end].strip()
        if line == "fi":
            return source_text[start_idx:line_end]
        if line:
            # A non-``fi`` non-empty line before ``fi`` means the merge-
            # failed ``if`` was already closed earlier. Bail out so the
            # test surfaces a clear "missing fi" error rather than a
            # confusing syntax error downstream.
            raise AssertionError(
                f"unexpected line between closer and closing ``fi``: "
                f"{line!r} (expected the merge-failed ``if`` to close "
                f"with ``fi`` right after the closer)"
            )
        cursor = line_end + 1
    raise AssertionError(
        "could not find closing ``fi`` after carve-out closer in source"
    )


def _write_stubs(bin_dir: Path, conflict_lines: list[str]) -> Path:
    """Install ``git`` and ``generate-doc-index.py`` stubs into ``bin_dir``.

    The carve-out runs *inside* the merge-failed branch. To exercise
    that branch without standing up a real git history, the ``git``
    stub must:

      * Make any ``merge`` invocation fail (exit 1) so
        ``if ! git -C "$WT" merge --no-ff ...; then`` evaluates the
        then-branch.
      * Return the supplied ``conflict_lines`` for any
        ``diff --name-only --diff-filter=U`` call (the conflict-set
        enumeration the carve-out uses to decide whether the conflict
        is in generated files).
      * No-op every other invocation
        (``checkout --theirs``, ``add -A``, ``commit --no-edit``) so
        the carve-out can complete without a real repository.

    ``generate-doc-index.py`` is also stubbed as an always-succeed
    no-op so the regenerate + ``--check --strict`` step runs without
    touching the filesystem. Returns ``bin_dir``.
    """
    git_stub = "#!/bin/bash\n"
    git_stub += "# Any `merge` invocation fails so the carve-out's then-branch runs.\n"
    git_stub += "for arg in \"$@\"; do\n"
    git_stub += "    if [ \"$arg\" = \"merge\" ]; then\n"
    git_stub += "        exit 1\n"
    git_stub += "    fi\n"
    git_stub += "done\n"
    git_stub += "if [[ \"$*\" == *\"--diff-filter=U\"* ]]; then\n"
    for line in conflict_lines:
        git_stub += f"printf '%s\\n' {shlex_quote(line)}\n"
    git_stub += "exit 0\nfi\n"
    git_stub += "exit 0\n"
    git_path = bin_dir / "git"
    git_path.write_text(git_stub)
    git_path.chmod(git_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    gen_stub = "#!/bin/bash\necho 'OK: stub generate-doc-index.py'\nexit 0\n"
    gen_path = bin_dir / "generate-doc-index.py"
    gen_path.write_text(gen_stub)
    gen_path.chmod(gen_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return bin_dir


def shlex_quote(value: str) -> str:
    """Bourne-shell single-quote a value. Avoids pulling in ``shlex`` (which
    has a side-effect of importing the whole stdlib module) for a single
    call site.
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _run_carve_out(
    carve_out: str,
    conflict_set: list[str],
    readme_content: str,
) -> subprocess.CompletedProcess[str]:
    """Run the carve-out bash against a controlled fixture.

    Sets up a temp ``$WT`` with a stubbed ``docs/cockpit/README.md`` and
    stubs ``git`` (returning ``conflict_set`` for the conflict
    enumeration) plus ``scripts/generate-doc-index.py`` (always
    succeeds). Returns the completed ``subprocess.run`` so the caller
    can inspect return code, stdout, and stderr.
    """
    worktree = Path(tempfile.mkdtemp(prefix="carve-out-test-"))
    bin_dir = Path(tempfile.mkdtemp(prefix="carve-out-stubs-"))
    try:
        # Place the README fixture
        docs = worktree / "docs" / "cockpit"
        docs.mkdir(parents=True)
        (docs / "README.md").write_text(readme_content, encoding="utf-8")
        # Place the llms.txt (the carve-out may `--theirs` it even when
        # not conflicted; the stub file just needs to exist)
        (docs / "llms.txt").write_text("# stub llms\n", encoding="utf-8")
        # Symlink scripts/generate-doc-index.py to the stub bin
        scripts = worktree / "scripts"
        scripts.mkdir()
        (scripts / "generate-doc-index.py").symlink_to(bin_dir / "generate-doc-index.py")
        # Install git + generate-doc-index.py stubs
        _write_stubs(bin_dir, conflict_set)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["WT"] = str(worktree)

        wrapper = f'WT="{worktree}"\n{carve_out}\n'
        return subprocess.run(
            ["bash", "-c", wrapper],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    finally:
        shutil.rmtree(worktree, ignore_errors=True)
        shutil.rmtree(bin_dir, ignore_errors=True)


def _readme_with_conflict(hunk_inside_markers: bool) -> str:
    """Build a README fixture with the standard markers plus a conflict hunk.

    ``hunk_inside_markers=True`` puts the conflict hunk between the
    markers (so the marker-boundary check passes); ``False`` puts it
    below the ``END`` marker (the carve-out must reject it).
    """
    lines = [
        "# Index",
        "",
        "Hand-curated preamble.",
        "",
        BEGIN_MARKER_LINE,
        "",
        "## Volledige index (gegenereerd)",
        "",
    ]
    if hunk_inside_markers:
        lines.extend([
            "<<<<<<< HEAD",
            "old generated table row",
            "=======",
            "new generated table row",
            ">>>>>>> branch-name",
            "",
        ])
    lines.extend([
        END_MARKER_LINE,
        "",
        "## Regels",
        "",
    ])
    if not hunk_inside_markers:
        lines.extend([
            "<<<<<<< HEAD",
            "old hand-curated prose",
            "=======",
            "new hand-curated prose",
            ">>>>>>> branch-name",
            "",
        ])
    return "\n".join(lines)


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_subset_readme_only_within_markers_succeeds(source_name: str) -> None:
    """A conflict in only README.md (hunk inside markers) must succeed.

    Pins the *subset-allowed* leg of the new carve-out: a conflict in
    only ``docs/cockpit/README.md`` (with the hunk inside the generated
    block) is the same class as both files conflicted — both files are
    regenerated from the merged frontmatter. The old exact-equality
    check would fall through to ``report_impediment`` for this case
    (kanban card ``72db7429…``).
    """
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=["docs/cockpit/README.md"],
        readme_content=_readme_with_conflict(hunk_inside_markers=True),
    )
    assert result.returncode == 0, (
        f"{source_name}: subset {{README.md}} within markers should be "
        f"auto-resolved by the carve-out, got exit "
        f"{result.returncode}. stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_subset_llms_only_succeeds(source_name: str) -> None:
    """A conflict in only llms.txt must succeed (regression pin)."""
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=["docs/cockpit/llms.txt"],
        readme_content=_readme_with_conflict(hunk_inside_markers=False),
    )
    assert result.returncode == 0, (
        f"{source_name}: subset {{llms.txt}} should be auto-resolved, "
        f"got exit {result.returncode}. stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_subset_readme_outside_markers_rejected(source_name: str) -> None:
    """A README.md conflict with a hunk outside the markers must be rejected.

    Pins the *marker-boundary* leg of the new carve-out: only the block
    between ``BEGIN/END GENERATED DOC INDEX`` is auto-regenerated; a
    conflict in the hand-curated prose (above or below the markers) must
    fall through to ``report_impediment`` so a human can resolve it.
    Without this check, the regenerate step would silently clobber
    hand-written content (kaart ``72db7429…``).
    """
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=["docs/cockpit/README.md"],
        readme_content=_readme_with_conflict(hunk_inside_markers=False),
    )
    assert result.returncode == 1, (
        f"{source_name}: README.md conflict with a hunk outside the "
        f"generated block must fall through to report_impediment, got "
        f"exit {result.returncode}. stderr={result.stderr!r}"
    )
    assert "outside" in result.stderr or "generated block" in result.stderr, (
        f"{source_name}: expected an explanatory error message "
        f"mentioning the marker boundary, got stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_non_generated_file_rejected(source_name: str) -> None:
    """A conflict in a non-generated file must fall through to report_impediment."""
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=["docs/cockpit/handwritten-spec.md"],
        readme_content=_readme_with_conflict(hunk_inside_markers=False),
    )
    assert result.returncode == 1, (
        f"{source_name}: handwritten file conflict must fall through, "
        f"got exit {result.returncode}. stderr={result.stderr!r}"
    )
    assert "handwritten" in result.stderr or "non-generated" in result.stderr or "report_impediment" in result.stderr, (
        f"{source_name}: expected an error naming the handwritten file, "
        f"got stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_exact_set_still_succeeds(source_name: str) -> None:
    """The original exact-set case (both files) must still succeed.

    Regression pin: the original (kanban card ``efb8187b…``) carve-out
    matched only the exact set ``{README.md, llms.txt}``. The new
    carve-out must accept that case as a subset, *and* keep accepting
    it — a refactor that broadens the predicate must not regress the
    legacy case.
    """
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=["docs/cockpit/README.md", "docs/cockpit/llms.txt"],
        readme_content=_readme_with_conflict(hunk_inside_markers=True),
    )
    assert result.returncode == 0, (
        f"{source_name}: exact set {{README.md, llms.txt}} should still "
        f"be auto-resolved, got exit {result.returncode}. "
        f"stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_mixed_generated_and_handwritten_rejected(source_name: str) -> None:
    """A conflict set mixing generated + handwritten files must fall through.

    Pins the *subset exclusion* leg: even though ``README.md`` is in
    the generated set, the conflict set as a whole contains a
    non-generated path, so the carve-out must reject. The old exact-
    equality check handled this case by accident; the new subset check
    must handle it explicitly (the non-generated exclusion is what
    ``comm -23`` computes).
    """
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=[
            "docs/cockpit/README.md",
            "docs/cockpit/handwritten-spec.md",
        ],
        readme_content=_readme_with_conflict(hunk_inside_markers=True),
    )
    assert result.returncode == 1, (
        f"{source_name}: conflict set with a handwritten file must "
        f"fall through, got exit {result.returncode}. "
        f"stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("source_name", sorted(SOURCES))
def test_empty_conflict_set_rejected(source_name: str) -> None:
    """An empty conflict set must fall through (defensive guard).

    A merge that fails with zero conflicted paths is structurally
    impossible (a merge conflict always has at least one unmerged
    file), but the carve-out's predicate should not silently succeed
    on ``CONFLICTED=""``. Without this guard, a future editor who
    refactors the conflict-set enumeration could feed an empty string
    through and the carve-out would execute the recovery path on a
    no-op.
    """
    carve_out = _extract_carve_out_block(SOURCES[source_name]())
    result = _run_carve_out(
        carve_out,
        conflict_set=[],
        readme_content=_readme_with_conflict(hunk_inside_markers=False),
    )
    assert result.returncode == 1, (
        f"{source_name}: empty conflict set must fall through, got "
        f"exit {result.returncode}. stderr={result.stderr!r}"
    )