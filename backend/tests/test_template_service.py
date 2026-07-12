"""Tests for TemplateService — the starter-content catalog for repo bootstrap.

See docs/cockpit/repo-provisioning-bootstrap.md §4.1. TemplateService owns the
`empty` / `python-fastapi` / `react-vite` templates and renders them onto a
target path with `{{ var }}` substitution.
"""
from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path

import pytest

from app.services.templates import TemplateDescriptor, TemplateService

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _relpaths(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_list_templates_returns_exactly_three():
    svc = TemplateService()
    templates = svc.list_templates()
    assert all(isinstance(t, TemplateDescriptor) for t in templates)
    names = {t.name for t in templates}
    assert names == {"empty", "python-fastapi", "react-vite"}
    for t in templates:
        assert t.description.strip()
        assert SEMVER.match(t.version), f"{t.name} version {t.version!r} not semver"


def test_render_empty_only_gitignore_and_readme(tmp_path):
    TemplateService().render("empty", tmp_path, vars={"project_name": "foo"})
    assert _relpaths(tmp_path) == {".gitignore", "README.md"}


def test_render_python_fastapi_substitutes_project_name(tmp_path):
    TemplateService().render("python-fastapi", tmp_path, vars={"project_name": "foo"})
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert re.search(r'name\s*=\s*"foo"', pyproject)
    assert (tmp_path / "app" / "main.py").exists()


def test_render_python_fastapi_structure_snapshot(tmp_path):
    TemplateService().render("python-fastapi", tmp_path, vars={"project_name": "foo"})
    assert _relpaths(tmp_path) == {
        "pyproject.toml",
        "requirements.txt",
        "Dockerfile",
        "app/__init__.py",
        "app/main.py",
        "tests/__init__.py",
        "tests/test_smoke.py",
    }


def test_render_python_fastapi_generates_compilable_python(tmp_path):
    TemplateService().render("python-fastapi", tmp_path, vars={"project_name": "foo"})
    for py in tmp_path.rglob("*.py"):
        py_compile.compile(str(py), doraise=True)


def test_render_react_vite_structure_snapshot(tmp_path):
    TemplateService().render("react-vite", tmp_path, vars={"project_name": "foo"})
    assert _relpaths(tmp_path) == {
        "package.json",
        "index.html",
        "tsconfig.json",
        "vite.config.ts",
        ".eslintrc.cjs",
        "src/main.tsx",
        "src/App.tsx",
    }


def test_render_react_vite_valid_json_with_deps(tmp_path):
    TemplateService().render("react-vite", tmp_path, vars={"project_name": "foo"})
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["name"] == "foo"
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "react" in deps
    assert "vite" in deps
    assert "typescript" in deps
    # tsconfig must also be valid JSON
    json.loads((tmp_path / "tsconfig.json").read_text())


def test_render_refuses_to_overwrite_without_flag(tmp_path):
    svc = TemplateService()
    svc.render("empty", tmp_path, vars={"project_name": "foo"})
    with pytest.raises(FileExistsError):
        svc.render("empty", tmp_path, vars={"project_name": "foo"})


def test_render_overwrite_flag_allows_rewrite(tmp_path):
    svc = TemplateService()
    svc.render("empty", tmp_path, vars={"project_name": "foo"})
    # Should not raise
    svc.render("empty", tmp_path, vars={"project_name": "foo"}, overwrite=True)


def test_render_is_idempotent(tmp_path):
    svc = TemplateService()
    svc.render("python-fastapi", tmp_path, vars={"project_name": "foo"})
    first = {p: (tmp_path / p).read_text() for p in _relpaths(tmp_path)}
    svc.render("python-fastapi", tmp_path, vars={"project_name": "foo"}, overwrite=True)
    second = {p: (tmp_path / p).read_text() for p in _relpaths(tmp_path)}
    assert first == second


def test_render_unknown_template_raises(tmp_path):
    with pytest.raises(KeyError):
        TemplateService().render("does-not-exist", tmp_path, vars={})


def test_render_accepts_matching_template_version(tmp_path):
    svc = TemplateService()
    version = next(t.version for t in svc.list_templates() if t.name == "empty")
    svc.render("empty", tmp_path, vars={"project_name": "foo"}, template_version=version)
    assert (tmp_path / ".gitignore").exists()


def test_render_rejects_unknown_template_version(tmp_path):
    with pytest.raises(ValueError):
        TemplateService().render(
            "empty", tmp_path, vars={"project_name": "foo"}, template_version="99.0.0"
        )


def test_render_leaves_no_tmpl_suffix_in_output(tmp_path):
    TemplateService().render("python-fastapi", tmp_path, vars={"project_name": "foo"})
    assert not any(p.name.endswith(".tmpl") for p in tmp_path.rglob("*"))
