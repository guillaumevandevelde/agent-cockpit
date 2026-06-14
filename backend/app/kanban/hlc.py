"""Hybrid Logical Clock: total, deterministic ordering across devices
despite wall-clock drift. Tick strings sort lexicographically by causal order.

Format: "<physical_ms:013d>:<logical:05d>:<node_id>".
"""
import time
from typing import Callable, Optional


def _default_now_ms() -> int:
    return int(time.time() * 1000)


def _format(physical: int, logical: int, node_id: str) -> str:
    return f"{physical:013d}:{logical:05d}:{node_id}"


def _physical(hlc: str) -> int:
    return int(hlc.split(":")[0])


def _logical(hlc: str) -> int:
    return int(hlc.split(":")[1])


class HLC:
    def __init__(self, node_id: str, _now_ms: Callable[[], int] = _default_now_ms):
        self.node_id = node_id
        self._now_ms = _now_ms
        self._last_physical = 0
        self._last_logical = 0

    def tick(self) -> str:
        """Generate the next local HLC (call when creating an op)."""
        pt = self._now_ms()
        if pt > self._last_physical:
            self._last_physical, self._last_logical = pt, 0
        else:
            self._last_logical += 1
        return _format(self._last_physical, self._last_logical, self.node_id)

    def update(self, remote_hlc: str) -> None:
        """Advance the clock to dominate a received remote HLC."""
        rp, rl = _physical(remote_hlc), _logical(remote_hlc)
        pt = self._now_ms()
        new_physical = max(self._last_physical, rp, pt)
        if new_physical == self._last_physical == rp:
            new_logical = max(self._last_logical, rl) + 1
        elif new_physical == self._last_physical:
            new_logical = self._last_logical + 1
        elif new_physical == rp:
            new_logical = rl + 1
        else:
            new_logical = 0
        self._last_physical, self._last_logical = new_physical, new_logical


def hlc_max(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the later of two HLCs (None-safe)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b
