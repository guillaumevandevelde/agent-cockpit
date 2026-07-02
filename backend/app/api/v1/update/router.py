"""Self-update API endpoints.

Triggers the update.sh script and streams its JSON-structured progress
via Server-Sent Events (SSE), following the same pattern as the
sandcastle /runs/{run_id}/stream endpoint.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["Update"])

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "update.sh"


async def _project_version() -> str:
    """Read the project version from VERSION file."""
    version_file = _PROJECT_ROOT / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


async def _git_commit() -> str:
    """Return the current HEAD git commit hash (short)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--short", "HEAD",
        cwd=_PROJECT_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0:
        return stdout.decode().strip()
    return "unknown"


async def _git_branch() -> str:
    """Return the current git branch name."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--abbrev-ref", "HEAD",
        cwd=_PROJECT_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0:
        return stdout.decode().strip()
    return "unknown"


async def _is_working_tree_clean() -> bool:
    """Check if the git working tree has uncommitted changes."""
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=_PROJECT_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return len(stdout.decode().strip()) == 0


async def _has_update_script() -> bool:
    """Check if the update script exists and is executable."""
    return _SCRIPT_PATH.is_file() and os.access(_SCRIPT_PATH, os.X_OK)


@router.get("/status")
async def update_status():
    """Return current version info and whether an update is possible.

    Used by the UI to show the current state before the user clicks update.
    """
    version, commit, branch, script_ok, clean = await asyncio.gather(
        _project_version(),
        _git_commit(),
        _git_branch(),
        _has_update_script(),
        _is_working_tree_clean(),
    )
    return {
        "version": version,
        "commit": commit,
        "branch": branch,
        "update_script_available": script_ok,
        "working_tree_clean": clean,
        "update_possible": script_ok and clean,
    }


@router.post("/run")
async def run_update(request: Request):
    """Execute the self-update and stream progress via SSE.

    Follows the same SSE pattern as sandcastle's /runs/{run_id}/stream.
    Each line from the script (JSON-structured) is forwarded as an SSE event.
    """
    if not _SCRIPT_PATH.is_file():
        raise HTTPException(status_code=404, detail="Update script not found")
    if not os.access(_SCRIPT_PATH, os.X_OK):
        raise HTTPException(status_code=500, detail="Update script not executable")

    async def event_generator():
        proc = await asyncio.create_subprocess_exec(
            "bash", str(_SCRIPT_PATH),
            cwd=_PROJECT_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Stream stdout line by line
        assert proc.stdout is not None
        try:
            while True:
                if await request.is_disconnected():
                    proc.terminate()
                    break

                line = await proc.stdout.readline()
                if not line:
                    break

                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue

                # The script emits JSON events; forward them as SSE
                try:
                    parsed = json.loads(raw)
                    event_type = parsed.get("event", "log")
                    yield f"event: {event_type}\ndata: {raw}\n\n"
                except json.JSONDecodeError:
                    # Non-JSON line (e.g. stray echo) — send as log event
                    yield f"event: log\ndata: {json.dumps({'event': 'log', 'message': raw})}\n\n"

            # Wait for process to exit and capture return code
            await proc.wait()
            code = proc.returncode or 1

            # Send final done/error event
            if code == 0:
                yield f"event: done\ndata: {json.dumps({'event': 'done', 'message': 'Update voltooid.', 'data': {'exit_code': 0}})}\n\n"
            else:
                yield f"event: error\ndata: {json.dumps({'event': 'error', 'message': 'Update mislukt — rollback toegepast.', 'data': {'exit_code': code}})}\n\n"
        except asyncio.CancelledError:
            proc.terminate()
            raise
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
