from app.kanban.hlc import HLC, hlc_max


def test_ticks_are_monotonic_and_sortable():
    clock = HLC(node_id="dev-a")
    a = clock.tick()
    b = clock.tick()
    assert a < b  # lexicographic string ordering == causal ordering


def test_same_physical_ms_increments_logical():
    clock = HLC(node_id="dev-a", _now_ms=lambda: 1000)
    a = clock.tick()
    b = clock.tick()
    assert a < b
    assert a.split(":")[0] == b.split(":")[0]  # same physical component


def test_update_pushes_clock_past_remote():
    clock = HLC(node_id="dev-a", _now_ms=lambda: 1000)
    remote = "9999999999999:00042:dev-b"
    clock.update(remote)
    nxt = clock.tick()
    assert nxt > remote


def test_hlc_max_returns_later():
    assert hlc_max("1:0:a", "2:0:a") == "2:0:a"
    assert hlc_max("2:0:a", None) == "2:0:a"
    assert hlc_max(None, "2:0:a") == "2:0:a"
