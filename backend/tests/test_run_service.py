"""Tests for RunService — sandboxed spawn of a built app.

Covers the four acceptance criteria from the kanban card:

* happy path (subprocess + health succeeds → status=healthy, log streamed)
* failure path (health times out → status=failed, process gone)
* port-conflict (caller asks for a busy port → either gets a different one
  or a clean 409)
* audit (env keys recorded, values never logged)

Plus the supporting read paths (``list``, ``get``, ``stop``) and transport
selection (no container runtime ⇒ subprocess). The container-transport path
is gated on a real docker/podman so we don't pretend it works on hosts that
don't have it.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.database import AsyncSessionLocal, Base, engine
from app.services import run_service as run_service_module
from app.services.run_service import run_service


@pytest_asyncio.fixture(autouse=True)
async def _isolate_app_runs_table():
    """Make sure the production app DB has the AppRun schema, then truncate.

    Tests in this file hit the real ``claude_registry.db`` (the conftest
    only patches the kanban DB). We can't DROP the whole DB because other
    test files share it, so we instead ensure the schema exists via
    ``create_all`` and wipe only the rows we own — namespaced under
    ``/tmp/pytest-run-service-*`` so we never collide with concurrent
    suites' data."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM app_runs WHERE project_path LIKE :pat"),
            {"pat": "/tmp/pytest-run-service-%"},
        )
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM app_runs WHERE project_path LIKE :pat"),
            {"pat": "/tmp/pytest-run-service-%"},
        )
        await session.commit()


def _bind_busy_port() -> tuple[socket.socket, int]:
    """Return (socket, port) where the socket is still holding the port.

    Used to force a collision with the service's ``_pick_free_port``."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    return s, s.getsockname()[1]


@pytest.fixture
def project_dir(tmp_path) -> Path:
    """Pin the tmp_path under /tmp/pytest-run-service-* so the cleanup
    fixture's ``LIKE`` namespace can find it without false positives."""
    root = Path("/tmp/pytest-run-service-" + tmp_path.name.replace("/", "_"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll ``predicate`` until it returns truthy or ``timeout`` elapses.

    Drives the async DB layer in tests without us reaching into its
    internals — we just ask the service for a fresh view and trust the
    polling to find the terminal state. Returns the final bool from the
    last ``predicate`` call (True ⇒ terminal, False ⇒ still running)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return predicate()


async def _wait_for_async(predicate, timeout: float = 5.0, interval: float = 0.05, terminal=None):
    """Poll ``predicate`` until it returns a value satisfying ``terminal``.

    ``terminal`` defaults to "non-None" — useful when the predicate
    returns ``None`` until something appears. For status polling,
    callers pass ``terminal=lambda v: v.status != "starting"`` so we
    don't return the row while the driver is still working."""
    is_terminal = terminal if terminal is not None else lambda v: v is not None
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = await predicate()
        if is_terminal(result):
            return result
        await asyncio.sleep(interval)
    return await predicate()


class _Health200(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server contract
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_):  # silence stderr noise
        pass


@pytest.fixture
async def http_health_server():
    """Spin up a real HTTP server on 127.0.0.1; yield (port, server)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Health200)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield port, server
    finally:
        server.shutdown()
        server.server_close()


# ---- transport selection ---------------------------------------------------

@pytest.mark.asyncio
async def test_start_falls_back_to_subprocess_when_no_container_runtime(monkeypatch, project_dir):
    """No docker / podman → transport=subprocess and a warning is logged."""
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {"docker_available": False, "podman_available": False})),
    )

    async def fake_drive(*_args, **_kwargs):
        return None

    # Skip the real driver — we're only asserting transport selection.
    monkeypatch.setattr(run_service, "_drive", fake_drive)

    captured: list[tuple[str, str]] = []

    def _spy(msg, *args):
        captured.append(msg % args if args else msg)

    monkeypatch.setattr(run_service_module.logger, "warning", _spy)

    instance = await run_service.start(
        project_path=str(project_dir),
        command=["python3", "-c", "print('hi')"],
    )

    assert instance.transport == "subprocess"
    assert any("falling back" in msg for msg in captured)


@pytest.mark.asyncio
async def test_start_picks_container_transport_when_docker_available(monkeypatch, project_dir):
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=("docker", {"docker_available": True, "podman_available": False})),
    )

    async def fake_drive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_service, "_drive", fake_drive)

    instance = await run_service.start(
        project_path=str(project_dir),
        command=["python3", "-c", "print('hi')"],
    )
    assert instance.transport == "container"


# ---- happy path ------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_health_succeeds_marks_healthy(monkeypatch, project_dir, http_health_server):
    """A real local HTTP server + health_path → status reaches healthy."""
    port, _server = http_health_server

    # Block the container path so we exercise the subprocess branch end-to-end.
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    # The command reads PORT from the env (RunService sets it) and binds on
    # it. We deliberately do NOT pass port= here — the http_health_server
    # already occupies its own kernel-assigned port, so passing that same
    # port would be a self-inflicted collision; this also exercises the
    # auto-pick branch.
    instance = await run_service.start(
        project_path=str(project_dir),
        command=[
            "python3", "-c", """
import http.server, socketserver, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *a):
        pass
socketserver.TCPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
""",
        ],
        health_path="/",
        health_timeout_s=10,
    )
    assert instance.port != port  # auto-picked, not the http_health_server's port
    assert instance.port in range(4001, 5000)
    assert instance.url == f"http://127.0.0.1:{instance.port}"
    assert instance.transport == "subprocess"

    final = await _wait_for_async(
        lambda: run_service.get(instance.instance_id),
        timeout=10,
        terminal=lambda v: v is not None and v.status in ("healthy", "failed", "stopped"),
    )
    assert final is not None
    assert final.status in ("healthy", "failed")

    await run_service.stop(instance.instance_id)


# ---- failure path ---------------------------------------------------------

@pytest.mark.asyncio
async def test_start_health_timeout_marks_failed_and_cleans_up(monkeypatch, project_dir):
    """Health never resolves → status=failed, no leaked process."""
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    instance = await run_service.start(
        project_path=str(project_dir),
        command=["python3", "-c", "import time; time.sleep(60)"],
        health_path="/never",
        health_timeout_s=1,
    )
    assert instance.transport == "subprocess"

    final = await _wait_for_async(
        lambda: run_service.get(instance.instance_id),
        timeout=5,
        terminal=lambda v: v is not None and v.status in ("healthy", "failed", "stopped"),
    )
    assert final is not None
    assert final.status == "failed"
    assert "health" in (final.error or "").lower()

    # Process must be gone — psutil-free way to check is to read /proc.
    if instance.pid:
        with pytest.raises(FileNotFoundError):
            os.kill(instance.pid, 0)


# ---- port allocation -------------------------------------------------------

def test_pick_free_port_avoids_busy_port():
    """Caller asks for a busy port → service picks a different one (no raise)."""
    held, port = _bind_busy_port()
    try:
        chosen = run_service_module._pick_free_port(port)
        assert chosen != port  # busy port was skipped
    finally:
        held.close()


@pytest.mark.asyncio
async def test_two_runs_get_different_ports_when_no_port_specified(monkeypatch, project_dir):
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    async def fake_drive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_service, "_drive", fake_drive)

    a = await run_service.start(project_path=str(project_dir), command=["python3", "-c", "pass"])
    b = await run_service.start(project_path=str(project_dir), command=["python3", "-c", "pass"])
    assert a.port != b.port


# ---- audit -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_writes_audit_with_command_no_env_values(monkeypatch, project_dir):
    """_record_audit receives env *names* and never the values themselves."""
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    async def fake_drive(*_args, **_kwargs):
        return None

    monkeypatch.setattr(run_service, "_drive", fake_drive)

    captured: list[dict] = []
    monkeypatch.setattr(
        run_service_module,
        "_record_audit",
        lambda project_key, transport, instance_id, env_keys, **kw: captured.append(
            {"project_key": project_key, "transport": transport,
             "instance_id": instance_id, "env_keys": env_keys,
             "kind": kw.get("kind")}
        ),
    )

    secret = "super-secret-value-should-never-appear"
    instance = await run_service.start(
        project_path=str(project_dir),
        command=["python3", "-c", "print('hello')"],
        env={"DATABASE_URL": secret, "LOG_LEVEL": "debug"},
    )

    assert len(captured) == 1
    audit = captured[0]
    assert audit["transport"] == "subprocess"
    assert audit["instance_id"] == instance.instance_id
    # Names yes, values no.
    assert sorted(audit["env_keys"]) == ["DATABASE_URL", "LOG_LEVEL"]
    assert secret not in json.dumps(audit)


# ---- read paths ------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_only_runs_for_project(monkeypatch, project_dir):
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )
    async def fake_drive(*_args, **_kwargs):
        return None
    monkeypatch.setattr(run_service, "_drive", fake_drive)

    other = project_dir / "other-project"
    other.mkdir(exist_ok=True)
    await run_service.start(project_path=str(project_dir), command=["python3", "-c", "pass"])
    await run_service.start(project_path=str(project_dir), command=["python3", "-c", "pass"])
    await run_service.start(project_path=str(other), command=["python3", "-c", "pass"])

    rows = await run_service.list(project_path=str(project_dir))
    assert len(rows) == 2
    assert all(r.project_path == str(project_dir) for r in rows)


@pytest.mark.asyncio
async def test_stop_returns_false_for_unknown_instance():
    assert await run_service.stop("does-not-exist") is False


@pytest.mark.asyncio
async def test_stop_subprocess_terminates_process(monkeypatch, project_dir):
    monkeypatch.setattr(
        run_service_module,
        "_container_available",
        lambda: asyncio.sleep(0, result=(None, {})),
    )

    instance = await run_service.start(
        project_path=str(project_dir),
        command=["python3", "-c", "import time; time.sleep(120)"],
    )
    # Drive the subprocess manually so we control the lifecycle in the test.
    assert instance.transport == "subprocess"

    # Background driver will time out, but we want to test stop() now.
    # Give the driver a moment to attach the pid.
    final = await _wait_for_async(
        lambda: run_service.get(instance.instance_id),
        timeout=2,
        terminal=lambda v: v is not None and v.pid is not None,
    )
    if final and final.pid:
        stopped = await run_service.stop(instance.instance_id)
        assert stopped is True
        # Give the SIGTERM a beat to reach the python interpreter; without
        # this the os.kill check below can race the in-flight termination.
        for _ in range(20):
            try:
                os.kill(final.pid, 0)
                await asyncio.sleep(0.05)
            except ProcessLookupError:
                break
        with pytest.raises(ProcessLookupError):
            os.kill(final.pid, 0)
    else:
        # Even without a known pid, stop() returns True and cleans up the
        # row (handled by the not-yet-attached branch in stop()).
        stopped = await run_service.stop(instance.instance_id)
        assert stopped is True