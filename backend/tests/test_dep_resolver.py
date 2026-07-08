from app.kanban.dep_resolver import detect_cycle, meets_dep_prerequisites


class _FakeCard:
    def __init__(self, id, depends_on=None, column=None):
        self.id = id
        self.depends_on = depends_on or []
        self.column = column


def test_meets_dep_prerequisites_no_deps():
    assert meets_dep_prerequisites(_FakeCard("c"), {}) is True


def test_meets_dep_prerequisites_all_parents_done():
    cards = {"parent": _FakeCard("parent", column="Done")}
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is True


def test_meets_dep_prerequisites_parent_not_done():
    cards = {"parent": _FakeCard("parent", column="Backlog")}
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is False


def test_meets_dep_prerequisites_missing_parent_fails_closed():
    cards = {}  # parent not in lookup
    assert meets_dep_prerequisites(_FakeCard("c", ["parent"]), cards) is False


def test_detect_cycle_no_cycle():
    assert detect_cycle({"a": ["b"], "b": ["c"], "c": []}) is None


def test_detect_cycle_finds_cycle():
    cycle = detect_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})
    assert cycle is not None
    assert cycle[0] == cycle[-1]


def test_detect_cycle_self_loop():
    cycle = detect_cycle({"a": ["a"]})
    assert cycle is not None
    assert "a" in cycle
