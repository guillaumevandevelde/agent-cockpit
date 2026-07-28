"""Pty relay — bridges a tmux pane to a WebSocket via pseudo-terminal."""
import asyncio
import ctypes
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import subprocess
import termios
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

_active_relays: dict[str, "PtyRelay"] = {}

# Linux PR_SET_PDEATHSIG: auto-SIGTERM the child when its parent dies.
# This prevents orphaned tmux attach-session processes on server reload/crash.
_PR_SET_PDEATHSIG = 1

# Per-relay pidfile directory. Each spawned tmux attach-session writes
# ``<pid>.pid`` here at spawn and removes it on close. ``cleanup_orphaned_relays``
# walks this dir instead of running a machine-wide ``pgrep -f`` — a previous
# implementation did the latter and SIGTERM'd relay processes owned by
# concurrent backends and human tmux viewers on the same box (kanban card
# 6069ea8b...). Lives under the canonical ``~/.claude-registry`` location
# alongside the existing pidfile directories (kanban headless-run pidfiles,
# blueprint store, secrets store).
_PIDFILE_DIR = Path.home() / ".claude-registry" / "pty-relays"


def is_target_interactive(target: str) -> bool:
    """Return whether a live relay for target currently accepts input."""
    relay = _active_relays.get(target)
    return relay is not None and not relay.read_only


def _child_preexec() -> None:
    """Set up the child process: new session + death signal."""
    os.setsid()
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except OSError:
        pass


def parse_control_message(text: str) -> dict | None:
    """Parse a text frame as a control message.

    Returns the parsed dict if it's valid JSON with a 'type' field,
    otherwise returns None (meaning it's terminal input).
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "type" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def resize_pty(
    fd: int,
    rows: int,
    cols: int,
    process: subprocess.Popen | None = None,
) -> None:
    """Resize the pty and signal the tmux attach process."""
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to resize pty: {e}")
        return

    # Send SIGWINCH so the tmux client notices the terminal size change
    if process and process.poll() is None:
        try:
            os.kill(process.pid, signal.SIGWINCH)
        except OSError:
            pass


class PtyRelay:
    """Bridges a tmux session to a WebSocket via a pseudo-terminal."""

    def __init__(self, target: str, read_only: bool = True):
        self.target = target
        self.read_only = read_only
        self.master_fd: int | None = None
        self.process: subprocess.Popen | None = None
        self._closed = False

    async def run(self, websocket: WebSocket) -> None:
        """Main relay loop — connect tmux to the WebSocket."""
        await websocket.accept()

        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd

        try:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            self.process = subprocess.Popen(
                ["tmux", "attach-session", "-t", self.target],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=_child_preexec,
                env=env,
            )
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            await websocket.send_json({"type": "error", "message": f"Failed to attach: {e}"})
            await websocket.close(code=4000)
            return

        # Register this relay's PID so ``cleanup_orphaned_relays`` knows it
        # belongs to us. Without this, a subsequent backend startup cannot
        # distinguish our (legitimately orphaned) relays from tmux
        # attach-session processes owned by other backends or human viewers
        # on the same box — see kanban card 6069ea8b… for the regression
        # that prompted the pidfile-based cleanup.
        _write_relay_pidfile(target=self.target, pid=self.process.pid)

        os.close(slave_fd)

        flag = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

        _active_relays[self.target] = self
        loop = asyncio.get_event_loop()

        output_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_pty_readable():
            try:
                data = os.read(master_fd, 65536)
                if data:
                    output_queue.put_nowait(data)
                else:
                    output_queue.put_nowait(b"")
            except OSError:
                output_queue.put_nowait(b"")

        loop.add_reader(master_fd, on_pty_readable)

        async def relay_output():
            try:
                while True:
                    data = await output_queue.get()
                    if not data:
                        break
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_bytes(data)
            except Exception as e:
                logger.debug(f"Output relay ended: {e}")

        async def relay_input():
            try:
                while True:
                    message = await websocket.receive()
                    msg_type = message.get("type", "")

                    if msg_type == "websocket.disconnect":
                        break

                    if "bytes" in message and message["bytes"]:
                        if not self.read_only:
                            os.write(master_fd, message["bytes"])
                        continue

                    text = message.get("text", "")
                    if not text:
                        continue

                    ctrl = parse_control_message(text)
                    if ctrl:
                        if ctrl["type"] == "resize":
                            resize_pty(master_fd, ctrl.get("rows", 24), ctrl.get("cols", 80), self.process)
                        elif ctrl["type"] == "mode":
                            self.read_only = ctrl.get("readOnly", True)
                    elif not self.read_only:
                        os.write(master_fd, text.encode())

            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.debug(f"Input relay ended: {e}")

        try:
            await asyncio.gather(relay_output(), relay_input())
        finally:
            self.close()
            loop.remove_reader(master_fd)
            _active_relays.pop(self.target, None)
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()

    def close(self) -> None:
        """Clean up pty and subprocess."""
        if self._closed:
            return
        self._closed = True

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

        if self.process:
            try:
                _remove_relay_pidfile(pid=self.process.pid)
            except Exception:
                # Pidfile cleanup must never break the close path —
                # a stale pidfile is a future-backend concern, not
                # this process's. Best-effort, log nothing (the
                # process is already shutting down).
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass


async def close_all_relays() -> None:
    """Close all active pty relays. Called on app shutdown."""
    for relay in list(_active_relays.values()):
        relay.close()
    _active_relays.clear()


def _relay_pidfile(pid: int) -> Path:
    """Return the pidfile path for ``pid`` (canonical: ``<pid>.pid``).

    The PID is the filename, not the body — a future reader can enumerate
    the directory without parsing every file. The body carries the target
    name for diagnostics only.
    """
    return _PIDFILE_DIR / f"{pid}.pid"


def _write_relay_pidfile(target: str, pid: int) -> None:
    """Persist ``<pid>.pid`` so a later startup can attribute the relay to us.

    Atomic enough for our purposes: ``write_text`` truncates + writes, and
    a crashed backend mid-write leaves a half-written pidfile whose body
    parse will simply fail (the filename still carries the PID for the
    enumeration path). Failures are logged but never raised — a missing
    pidfile just means this backend won't clean up its own orphans on the
    next restart, which is a soft regression compared to the machine-wide
    ``pgrep -f`` SIGTERM-storm we replaced.
    """
    try:
        _PIDFILE_DIR.mkdir(parents=True, exist_ok=True)
        _relay_pidfile(pid).write_text(target, encoding="utf-8")
    except OSError:
        logger.exception("could not write relay pidfile for pid=%s target=%s", pid, target)


def _remove_relay_pidfile(pid: int) -> None:
    """Remove ``<pid>.pid`` if present. Tolerant of missing/already-gone.

    Unlike the kanban headless-run pidfile path, the filename already
    encodes the PID — there is no race where another run has overwritten
    our pidfile with a different PID, since each PID is unique system-wide.
    """
    try:
        _relay_pidfile(pid).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("could not remove relay pidfile for pid=%s", pid)


def cleanup_orphaned_relays() -> None:
    """SIGTERM orphaned relay processes from previous runs.

    Scoped to PIDs this backend itself owns via the pidfile directory
    ``~/.claude-registry/pty-relays/``. Walks that directory instead of
    running a machine-wide ``pgrep -f`` — the previous implementation did
    the latter and SIGTERM'd every ``tmux attach-session`` on the box,
    including relay processes owned by concurrent backends and human tmux
    attach viewers. PR_SET_PDEATHSIG handles the normal case; this is a
    safety net for when the parent was SIGKILL'd (no PR_SET_PDEATHSIG
    fires), see kanban card 6069ea8b… for the regression that prompted
    the pidfile-based cleanup.

    A pidfile alone is not enough — every backend under the same user
    shares the directory, so a concurrent still-running backend would
    otherwise have its relays SIGTERM'd by a different backend's startup.
    The fix disambiguates by reading the relay's PPID from ``/proc``:
    if the parent is alive (whether it's us or another backend), the
    relay is still owned — leave it alone. Only when the parent is gone
    is the relay genuinely orphaned and safe to kill.

    Stale pidfiles (referring to PIDs that no longer exist) are pruned so
    the directory doesn't accumulate garbage from prior backends. This
    is its own case, separate from the orphan-ownership check: when the
    relay itself is gone there's nothing to kill, only a file to remove.
    """
    try:
        try:
            entries = list(_PIDFILE_DIR.iterdir())
        except FileNotFoundError:
            # No pidfile dir yet means no relay has ever been spawned
            # from this machine — nothing to clean up.
            return
        killed = 0
        pruned = 0
        for entry in entries:
            if not entry.name.endswith(".pid"):
                continue
            try:
                pid = int(entry.name.removesuffix(".pid"))
            except ValueError:
                # Malformed filename — drop it so it doesn't stick around.
                try:
                    entry.unlink()
                except OSError:
                    pass
                continue
            # First gate: is the relay still alive at all? A PID that
            # no longer exists is a stale pidfile (the relay exited
            # cleanly between write and cleanup); prune the file and
            # move on. Distinguishing "PID gone" from "PID alive with
            # a dead parent" matters because the former wants no
            # SIGTERM (nothing to kill) while the latter wants one.
            if not _is_pid_alive(pid):
                try:
                    entry.unlink()
                    pruned += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                continue
            # PID is alive — now check whether it's orphaned by PPID.
            if not _is_orphaned_relay(pid):
                # The relay is still owned (by us, or by a concurrent
                # backend). Leave the pidfile in place — PtyRelay.close()
                # or the next cleanup of the owning backend will remove
                # it.
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except ProcessLookupError:
                # PID died between the alive check and the SIGTERM —
                # treat as success for accounting purposes; the file
                # is stale either way.
                pass
            except OSError:
                # Other ESRCH-equivalents / permission errors — skip
                # without removing the pidfile; next run can retry.
                continue
            finally:
                try:
                    entry.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        if killed:
            logger.info(
                "Cleaned up %d orphaned relay process(es) from prior runs",
                killed,
            )
        if pruned:
            logger.info(
                "Pruned %d stale relay pidfile(s) from prior runs",
                pruned,
            )
    except Exception:
        logger.exception("relay cleanup encountered an unexpected error")


def _is_pid_alive(pid: int) -> bool:
    """Return True iff ``pid`` is a live process we can signal.

    Uses signal 0 — a no-op probe that succeeds only when the kernel
    recognises the PID as a process we own or could signal. This is the
    standard POSIX liveness check; cheaper than reading ``/proc/<pid>``
    and works across any PID-namespace quirk.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # EPERM means the PID exists but we lack permission — alive
        # for our purposes (we wouldn't want to kill it anyway).
        return True
    return True


def _PROC_STATUS_PATH(pid: int) -> Path:
    """Path to ``/proc/<pid>/status`` for PPID lookup. Linux-only."""
    return Path(f"/proc/{pid}/status")


def _read_ppid(pid: int) -> int | None:
    """Return the parent PID of ``pid``, or None if unavailable.

    Reads the kernel-provided ``/proc/<pid>/status`` — no syscalls, no
    dependencies, just a parse. Returns None when the process is gone
    (status file missing) or the status file is unparseable. Callers
    must treat None as "we can't tell" rather than "orphan".
    """
    try:
        with _PROC_STATUS_PATH(pid).open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def _is_orphaned_relay(pid: int) -> bool:
    """Return True iff ``pid`` is alive and its parent is no longer alive.

    Distinguishes the three relay populations on the box:

    * **Owned by us (current backend):** PPID == ``os.getpid()`` — we are
      its parent and alive. Skip — never kill our own live relay.
    * **Owned by a concurrent backend:** PPID != us but PPID is alive.
      Skip — that backend's ``PtyRelay.close()`` will handle its own
      pidfile when the relay exits.
    * **Genuinely orphaned:** PPID is dead (the previous backend was
      SIGKILL'd and never ran its close path). This is the only case
      we should SIGTERM.

    Returns False when the PPID can't be read (process gone or unreadable)
    — a stale pidfile whose process is already dead is pruned by the
    caller regardless, so the kill decision is moot here.
    """
    ppid = _read_ppid(pid)
    if ppid is None:
        return False
    if ppid == os.getpid():
        return False
    try:
        os.kill(ppid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        # Permission denied or other — we can't prove the parent is dead.
        # Be conservative: don't kill a relay whose ownership we can't
        # establish. The next backend startup can retry if the parent
        # dies in the meantime.
        return False
    return False
