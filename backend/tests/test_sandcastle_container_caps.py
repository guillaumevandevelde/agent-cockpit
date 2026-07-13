"""Integration tests for the sandcastle resource/network caps.

These drive a *real* container runtime with the exact flags
`_container_security_flags` / `_network_option` emit, proving the three
acceptance-criteria behaviours end-to-end:

  - memory_limit_mb=256 → the run OOM-kills predictably
  - read_only_rootfs=true → /etc/hostname cannot be overwritten
  - network_mode=none → outbound DNS fails

They require a container runtime *and* the sandcastle image (which ships `node`
+ `sh`, used to allocate memory / resolve DNS deterministically). Both are
absent in CI, so the whole module skips there; it runs for a developer who has
built `sandcastle:local` locally. The flag *contract* itself (which flags each
config field emits) is covered CI-side in test_sandcastle_service.py.
"""
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services.sandcastle_service import (
    _container_security_flags,
    _network_option,
)

_RUNTIME = shutil.which("docker") or shutil.which("podman")
_IMAGE = "sandcastle:local"


def _image_present() -> bool:
    if not _RUNTIME:
        return False
    return (
        subprocess.run(
            [_RUNTIME, "image", "inspect", _IMAGE],
            capture_output=True,
        ).returncode
        == 0
    )


pytestmark = pytest.mark.skipif(
    not _image_present(),
    reason="requires a container runtime + locally-built sandcastle:local image",
)


def _run(extra_flags, cmd, timeout=120):
    return subprocess.run(
        [_RUNTIME, "run", "--rm", *extra_flags, _IMAGE, *cmd],
        capture_output=True,
        timeout=timeout,
    )


def _caps(**kw):
    base = dict(memory_limit_mb=None, pids_limit=None, read_only_rootfs=False)
    base.update(kw)
    return _container_security_flags(SimpleNamespace(**base))


def test_memory_limit_oom_kills_run():
    flags = _caps(memory_limit_mb=256)
    # Allocate 10MB chunks past the 256MB cap; --memory-swap==--memory (no swap)
    # means the kernel OOM-kills the process instead of letting it grow.
    result = _run(
        flags,
        ["node", "-e", "const a=[];for(;;)a.push(Buffer.alloc(10*1024*1024).fill(1))"],
    )
    assert result.returncode != 0  # typically 137 (SIGKILL from the OOM killer)


def test_read_only_rootfs_blocks_etc_write():
    flags = _caps(read_only_rootfs=True)
    # Run as root (0:0) so the block is the read-only rootfs, not file perms.
    blocked = _run(["--user", "0:0", *flags], ["sh", "-c", "echo pwned > /etc/hostname"])
    assert blocked.returncode != 0
    # Control: same root write succeeds when the rootfs is writable.
    allowed = _run(["--user", "0:0"], ["sh", "-c", "echo pwned > /etc/hostname"])
    assert allowed.returncode == 0


def test_network_none_blocks_dns():
    net = _network_option("none")
    assert net == "none"
    # exit 7 == our sentinel for "lookup failed" (i.e. no outbound DNS).
    result = _run(
        ["--network", net],
        ["node", "-e", "require('dns').lookup('example.com',e=>process.exit(e?7:0))"],
    )
    assert result.returncode == 7
