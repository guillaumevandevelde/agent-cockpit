"""Unit tests for the pure merge helpers in `check_openapi_snapshot`.

The targeted `--update` mode isolates *this branch's* API changes and applies
only those onto the committed snapshot, so pre-existing drift from older
commits is never dragged in. `three_way_merge` is that logic; the git/worktree
plumbing that produces the merge-base spec is exercised separately (and only
runs when a developer actually invokes `--update`), so we drive the pure merge
directly here rather than shelling out to a whole app import.
"""
from __future__ import annotations

from scripts.check_openapi_snapshot import (
    _changed_line_count,
    snapshot_text,
    targeted_update_stalled,
    three_way_merge,
    unified_diff,
)

# A "drift" key is present in the merge-base spec (`base`) and inherited by the
# branch (`current`) but was never written to the committed `snapshot`.


def test_targeted_update_applies_only_branch_deletions():
    # Branch deletes two endpoints + one schema. `base` (merge-base) still has
    # them plus drift the snapshot never captured.
    base = {
        "paths": {
            "/keep": {"get": {"summary": "keep"}},
            "/api/v1/kanban/max-sessions": {"get": {"summary": "doomed-1"}},
            "/api/v1/kanban/max-sessions-set": {"post": {"summary": "doomed-2"}},
            "/drift/new": {"get": {"summary": "added-by-earlier-commit"}},
        },
        "components": {
            "schemas": {
                "KeepSchema": {"type": "object"},
                "MaxSessionsRequest": {"type": "object"},
                "DriftSchema": {"type": "object"},
            }
        },
    }
    # current = base minus the two endpoints and the schema (branch's deletion),
    # drift still present because the branch inherited it.
    current = {
        "paths": {
            "/keep": {"get": {"summary": "keep"}},
            "/drift/new": {"get": {"summary": "added-by-earlier-commit"}},
        },
        "components": {
            "schemas": {
                "KeepSchema": {"type": "object"},
                "DriftSchema": {"type": "object"},
            }
        },
    }
    # snapshot = stale committed snapshot: has the doomed items, lacks drift.
    snapshot = {
        "paths": {
            "/keep": {"get": {"summary": "keep"}},
            "/api/v1/kanban/max-sessions": {"get": {"summary": "doomed-1"}},
            "/api/v1/kanban/max-sessions-set": {"post": {"summary": "doomed-2"}},
        },
        "components": {
            "schemas": {
                "KeepSchema": {"type": "object"},
                "MaxSessionsRequest": {"type": "object"},
            }
        },
    }

    merged = three_way_merge(snapshot, base, current)

    # Exactly the doomed items removed; drift never pulled in.
    assert merged == {
        "paths": {"/keep": {"get": {"summary": "keep"}}},
        "components": {"schemas": {"KeepSchema": {"type": "object"}}},
    }


def test_targeted_update_applies_branch_addition_without_drift():
    base = {
        "paths": {"/keep": {"get": {}}, "/drift": {"get": {}}},
        "components": {"schemas": {}},
    }
    current = {
        "paths": {
            "/keep": {"get": {}},
            "/drift": {"get": {}},
            "/new": {"post": {"summary": "branch-added"}},
        },
        "components": {"schemas": {}},
    }
    snapshot = {"paths": {"/keep": {"get": {}}}, "components": {"schemas": {}}}

    merged = three_way_merge(snapshot, base, current)

    assert merged["paths"] == {
        "/keep": {"get": {}},
        "/new": {"post": {"summary": "branch-added"}},
    }


def test_targeted_update_merges_nested_change_keeping_drift_sibling():
    # Branch changes /foo's get summary. /foo also has a "post" op that is drift
    # (in base+current, not in snapshot). The nested change must apply while the
    # drift sibling stays out.
    base = {
        "paths": {"/foo": {"get": {"summary": "v1"}, "post": {"x": 1}}},
        "components": {"schemas": {}},
    }
    current = {
        "paths": {"/foo": {"get": {"summary": "v2"}, "post": {"x": 1}}},
        "components": {"schemas": {}},
    }
    snapshot = {
        "paths": {"/foo": {"get": {"summary": "v1"}}},
        "components": {"schemas": {}},
    }

    merged = three_way_merge(snapshot, base, current)

    assert merged["paths"]["/foo"] == {"get": {"summary": "v2"}}


def test_no_branch_change_leaves_snapshot_untouched():
    base = {"paths": {"/a": {}, "/drift": {}}, "components": {"schemas": {}}}
    current = {"paths": {"/a": {}, "/drift": {}}, "components": {"schemas": {}}}
    snapshot = {"paths": {"/a": {}}, "components": {"schemas": {}}}

    assert three_way_merge(snapshot, base, current) == snapshot


def test_stalled_when_targeted_update_is_a_noop_but_drift_remains():
    # The master-drift case: an endpoint was deleted in a commit that landed on
    # master without updating the snapshot. On master, merge-base(HEAD,
    # origin/master) == HEAD, so base == current and three_way_merge returns the
    # snapshot untouched -- while that snapshot still disagrees with the live
    # spec. Reporting "already up to date" there is what kept quality.yml red:
    # the check's failure message recommends exactly this no-op command.
    current = {"paths": {"/keep": {"get": {}}}, "components": {"schemas": {}}}
    merged = {
        "paths": {"/keep": {"get": {}}, "/gone": {"post": {}}},
        "components": {"schemas": {}},
    }

    assert targeted_update_stalled(merged, current) is True


def test_not_stalled_when_merged_result_matches_live_spec():
    current = {"paths": {"/keep": {"get": {}}}, "components": {"schemas": {}}}

    assert targeted_update_stalled(dict(current), current) is False


def test_stalled_ignores_env_dependent_root_path():
    # "/" is stripped from the compared shape (it exists only when frontend/dist
    # is on disk), so a snapshot carrying it is not drift.
    current = {"paths": {"/keep": {"get": {}}}, "components": {"schemas": {}}}
    merged = {
        "paths": {"/keep": {"get": {}}, "/": {"get": {}}},
        "components": {"schemas": {}},
    }

    assert targeted_update_stalled(merged, current) is False


def test_unified_diff_empty_when_identical_and_counts_changes():
    shape = {"paths": {"/a": {}}, "components": {"schemas": {}}}
    text = snapshot_text(shape)
    assert unified_diff(text, text) == ""

    changed = {"paths": {"/a": {}, "/b": {}}, "components": {"schemas": {}}}
    diff = unified_diff(text, snapshot_text(changed))
    assert diff != ""
    assert _changed_line_count(diff) >= 1
