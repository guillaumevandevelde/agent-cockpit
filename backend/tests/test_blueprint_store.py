"""Tests for BlueprintStore — the file-backed CRUD over `Blueprint` documents.

Mirrors `backend/app/services/blueprint/store.py`. Sibling card `395590d7`
delivered the apply-engine; this test file covers the storage layer that the
REST CRUD and frontend UI sit on top of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.blueprint import Blueprint, BlueprintAgent, BlueprintSettings, BlueprintSkill
from app.services.blueprint.store import (
    BlueprintAlreadyExists,
    BlueprintNameError,
    BlueprintNotFound,
    BlueprintStore,
)


@pytest.fixture
def store(tmp_path) -> BlueprintStore:
    """A fresh, tmp-path store per test — never touches the real store dir."""
    return BlueprintStore(root=tmp_path)


# -- name validation -------------------------------------------------------


def test_validate_name_accepts_slug(store):
    assert store.validate_name("webapp-default") == "webapp-default"
    assert store.validate_name("cli.minimal") == "cli.minimal"
    assert store.validate_name("a") == "a"  # single char OK


@pytest.mark.parametrize(
    "bad_name",
    [
        "",                        # empty
        " ",                       # whitespace only
        "Has-Space",               # uppercase
        "with/slash",              # slash (path traversal)
        "../escape",               # dot-dot traversal
        ".dot",                    # leading dot
        "a" * 65,                  # too long
        ".tmp",                    # reserved
    ],
)
def test_validate_name_rejects_bad_inputs(bad_name):
    with pytest.raises(BlueprintNameError):
        BlueprintStore.validate_name(bad_name)


# -- CRUD -------------------------------------------------------------------


def test_list_empty_store_returns_empty(store):
    assert store.list() == []


def test_save_and_get_roundtrip(store):
    bp = Blueprint(
        name="webapp",
        description="Web app default",
        skills=[BlueprintSkill(name="react", version_pin="18.0")],
        agents=[BlueprintAgent(name="planner", model_default="opus", tools=["Read", "Write"])],
        statusline='#!/bin/sh\necho "hi"\n',
        output_style="concise",
        claudemd="# context\n",
    )
    saved = store.save(bp)

    assert saved.created_at is not None
    assert saved.updated_at is not None

    loaded = store.get("webapp")
    assert loaded.name == "webapp"
    assert loaded.description == "Web app default"
    assert loaded.skills[0].name == "react"
    assert loaded.skills[0].version_pin == "18.0"
    assert loaded.agents[0].model_default == "opus"
    assert loaded.agents[0].tools == ["Read", "Write"]
    assert loaded.statusline and loaded.statusline.startswith("#!/bin/sh")
    assert loaded.output_style == "concise"
    assert loaded.claudemd == "# context\n"


def test_save_sets_created_at_only_on_first_save(store):
    bp1 = Blueprint(name="a", description="v1")
    saved1 = store.save(bp1)
    first_created = saved1.created_at

    bp2 = Blueprint(name="a", description="v2")
    saved2 = store.save(bp2)
    # `created_at` survives; `updated_at` is bumped.
    assert saved2.created_at == first_created
    assert saved2.updated_at is not None
    assert saved2.updated_at >= saved1.updated_at  # type: ignore[operator]
    assert store.get("a").description == "v2"


def test_save_rejects_duplicate_when_overwrite_false(store):
    store.save(Blueprint(name="x"))
    with pytest.raises(BlueprintAlreadyExists):
        store.save(Blueprint(name="x"), overwrite=False)


def test_get_missing_raises_not_found(store):
    with pytest.raises(BlueprintNotFound):
        store.get("nope")


def test_delete_removes_file(store):
    store.save(Blueprint(name="to-delete"))
    store.delete("to-delete")
    with pytest.raises(BlueprintNotFound):
        store.get("to-delete")


def test_delete_missing_raises_not_found(store):
    with pytest.raises(BlueprintNotFound):
        store.delete("never-existed")


def test_list_returns_all_blueprints_sorted(store):
    store.save(Blueprint(name="zebra"))
    store.save(Blueprint(name="alpha"))
    store.save(Blueprint(name="mango"))
    names = [bp.name for bp in store.list()]
    assert names == ["alpha", "mango", "zebra"]


def test_list_skips_unreadable_files(store):
    store.save(Blueprint(name="good"))
    # Drop a non-JSON file in the store dir; list() must not raise.
    (store.root / "garbage.json").write_text("not json")
    blueprints = store.list()
    assert [bp.name for bp in blueprints] == ["good"]


def test_atomic_save_leaves_no_partial_files(store):
    """A successful save leaves exactly one .json file — never a .tmp."""
    store.save(Blueprint(name="atomic"))
    files = sorted(p.name for p in store.root.iterdir())
    assert files == ["atomic.json"]
    assert not list(store.root.glob("*.tmp"))


def test_save_preserves_extra_settings(store):
    """BlueprintSettings.extra fields round-trip (CC may carry hooks, env, ...)."""
    settings = BlueprintSettings.model_validate(
        {"permission_mode": "plan", "model": "opus", "env": {"FOO": "bar"}},
    )
    store.save(Blueprint(name="with-extras", settings=settings))
    loaded = store.get("with-extras")
    assert loaded.settings.permission_mode == "plan"
    assert loaded.settings.model == "opus"
    assert loaded.settings.model_extra == {"env": {"FOO": "bar"}}  # type: ignore[attr-defined]