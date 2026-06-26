"""Tests for git branch-name sanitization (git check-ref-format rules)."""
import pytest

from app.utils.git_ref import sanitize_git_branch_name


def test_passes_through_already_valid_name():
    assert sanitize_git_branch_name("feature-foo") == "feature-foo"


def test_keeps_namespacing_slashes():
    assert sanitize_git_branch_name("feature/foo") == "feature/foo"


def test_spaces_become_dashes():
    assert sanitize_git_branch_name("feature/foo bar") == "feature/foo-bar"


def test_strips_leading_dash():
    assert sanitize_git_branch_name("-x") == "x"


def test_strips_leading_and_trailing_dot():
    assert sanitize_git_branch_name(".foo.") == "foo"


def test_double_dot_is_forbidden():
    assert sanitize_git_branch_name("foo..bar") == "foo-bar"


def test_replaces_forbidden_characters():
    assert sanitize_git_branch_name("foo~bar:baz^qux?[*\\") == "foo-bar-baz-qux"


def test_drops_trailing_dot_lock():
    assert sanitize_git_branch_name("foo.lock") == "foo"


def test_replaces_at_brace_sequence():
    # "@{" is forbidden; "}" alone is allowed by git.
    assert sanitize_git_branch_name("foo@{1}") == "foo-1}"


def test_collapses_empty_path_components_and_trims_slashes():
    assert sanitize_git_branch_name("//foo//bar//") == "foo/bar"


def test_collapses_repeated_dashes():
    assert sanitize_git_branch_name("foo   bar") == "foo-bar"


def test_raises_when_nothing_usable_remains():
    with pytest.raises(ValueError):
        sanitize_git_branch_name("...")
    with pytest.raises(ValueError):
        sanitize_git_branch_name("   ")
    with pytest.raises(ValueError):
        sanitize_git_branch_name("@")
