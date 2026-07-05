"""Service for managing remote hosts and executing commands via SSH."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import Host

logger = logging.getLogger(__name__)

HOST_SCHEMAS: dict[str, type] = {}


def _register_schemas(module: Any) -> None:
    """Late-bind Pydantic schemas from the caller so the model module stays pure."""
    for name in ("HostCreate", "HostUpdate", "HostResponse"):
        HOST_SCHEMAS[name] = getattr(module, name)


class HostNotFoundError(ValueError):
    """Raised when a host ID does not exist."""

    pass


async def create_host(db: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    """Create a new host entry."""
    host = Host(
        alias=data["alias"],
        hostname=data["hostname"],
        port=data.get("port", 22),
        username=data["username"],
        ssh_key_path=data.get("ssh_key_path"),
        status="unknown",
    )
    db.add(host)
    await db.flush()
    await db.refresh(host)
    return _host_to_dict(host)


async def get_host(db: AsyncSession, host_id: int) -> dict[str, Any]:
    """Get a single host by ID."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HostNotFoundError(f"Host with id {host_id} not found")
    return _host_to_dict(host)


async def list_hosts(db: AsyncSession) -> list[dict[str, Any]]:
    """List all registered hosts."""
    result = await db.execute(select(Host).order_by(Host.alias))
    return [_host_to_dict(h) for h in result.scalars().all()]


async def update_host(
    db: AsyncSession, host_id: int, data: dict[str, Any]
) -> dict[str, Any]:
    """Update an existing host."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HostNotFoundError(f"Host with id {host_id} not found")

    for field in ("alias", "hostname", "port", "username", "ssh_key_path", "status"):
        if field in data:
            setattr(host, field, data[field])
    host.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(host)
    return _host_to_dict(host)


async def delete_host(db: AsyncSession, host_id: int) -> None:
    """Delete a host."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HostNotFoundError(f"Host with id {host_id} not found")
    await db.delete(host)
    await db.flush()


async def test_connection(host: dict[str, Any]) -> bool:
    """Test SSH connectivity to a host.

    Runs ``ssh -o ConnectTimeout=5 -o BatchMode=yes <host> exit`` and returns
    True if the exit code is 0.
    """
    ssh_cmd = build_ssh_base(host) + ["exit"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        code = await proc.wait()
        return code == 0
    except (FileNotFoundError, OSError) as exc:
        logger.warning("SSH connection test failed for %s: %s", host["alias"], exc)
        return False


async def execute_remote_tmux(
    host: dict[str, Any],
    tmux_args: list[str],
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a tmux command on a remote host via SSH.

    Returns (exit_code, stdout, stderr).
    """
    ssh_cmd = build_ssh_base(host) + ["tmux"] + tmux_args
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return (
                proc.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return (-1, "", f"SSH command timed out after {timeout}s")
    except FileNotFoundError:
        return (-1, "", "ssh binary not found")
    except OSError as exc:
        return (-1, "", str(exc))


async def discover_remote_agent_sessions(
    host: dict[str, Any],
) -> list[dict[str, Any]]:
    """Discover agent sessions on a remote host by running ``tmux list-panes``.

    Returns a list of session dicts compatible with the agent-bridge discovery
    format, with an added ``host_id`` and ``host_alias`` field.
    """
    _PANE_FORMAT = (
        "#{session_name}:#{window_index}.#{pane_index}"
        "|#{session_name}"
        "|#{window_name}"
        "|#{pane_id}"
        "|#{pane_current_path}"
        "|#{pane_pid}"
        "|#{pane_current_command}"
    )
    rc, stdout, stderr = await execute_remote_tmux(
        host, ["list-panes", "-a", "-F", _PANE_FORMAT]
    )
    if rc != 0:
        logger.debug(
            "Remote tmux list-panes failed on %s: %s", host["alias"], stderr
        )
        return []

    sessions: list[dict[str, Any]] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|", 6)
        if len(parts) != 7:
            continue
        target, session_name, window_name, pane_id, cwd, pid, command = parts
        sessions.append(
            {
                "provider": "remote",
                "provider_display_name": f"Remote ({host['alias']})",
                "tmux_target": target,
                "session_name": session_name,
                "window_name": window_name,
                "pane_id": pane_id,
                "cwd": cwd,
                "pid": pid,
                "status": "active",
                "host_id": host["id"],
                "host_alias": host["alias"],
            }
        )
    return sessions


def build_ssh_base(host: dict[str, Any]) -> list[str]:
    """Build the base SSH command with ControlMaster for a host."""
    cmd = [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=/tmp/ssh-cm-%r@%h:%p",
        "-o", "ControlPersist=60",
        "-p", str(host["port"]),
    ]
    if host.get("ssh_key_path"):
        cmd += ["-i", host["ssh_key_path"]]
    cmd.append(f"{host['username']}@{host['hostname']}")
    return cmd


def _host_to_dict(host: Host) -> dict[str, Any]:
    """Convert a Host ORM instance to a plain dict for API responses."""
    return {
        "id": host.id,
        "alias": host.alias,
        "hostname": host.hostname,
        "port": host.port,
        "username": host.username,
        "ssh_key_path": host.ssh_key_path,
        "status": host.status,
        "created_at": host.created_at.isoformat() if host.created_at else None,
        "updated_at": host.updated_at.isoformat() if host.updated_at else None,
    }
