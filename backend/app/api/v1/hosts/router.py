"""API routes for the host registry.

Manages remote machines that can run Claude Code / Codex CLI sessions,
and provides endpoints for testing SSH connectivity.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.host_service import (
    HostNotFoundError,
    create_host,
    delete_host,
    discover_remote_agent_sessions,
    get_host,
    list_hosts,
    test_connection,
    update_host,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Pydantic schemas ---


class HostCreate(BaseModel):
    alias: str = Field(..., min_length=1, max_length=100)
    hostname: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=100)
    ssh_key_path: str | None = None


class HostUpdate(BaseModel):
    alias: str | None = None
    hostname: str | None = None
    port: int | None = None
    username: str | None = None
    ssh_key_path: str | None = None


class HostResponse(BaseModel):
    id: int
    alias: str
    hostname: str
    port: int
    username: str
    ssh_key_path: str | None = None
    status: str = "unknown"
    created_at: str | None = None
    updated_at: str | None = None


class HostTestResponse(BaseModel):
    reachable: bool
    alias: str
    hostname: str


class HostListResponse(BaseModel):
    hosts: list[HostResponse]


# --- Endpoints ---


@router.get("/hosts", response_model=HostListResponse)
async def list_hosts_endpoint(db: AsyncSession = Depends(get_db)):
    hosts = await list_hosts(db)
    return HostListResponse(hosts=[HostResponse(**h) for h in hosts])


@router.post("/hosts", response_model=HostResponse, status_code=201)
async def create_host_endpoint(
    data: HostCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        host = await create_host(db, data.model_dump())
        return HostResponse(**host)
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail=f"Host alias '{data.alias}' already exists")
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/hosts/{host_id}", response_model=HostResponse)
async def get_host_endpoint(
    host_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        host = await get_host(db, host_id)
        return HostResponse(**host)
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/hosts/{host_id}", response_model=HostResponse)
async def update_host_endpoint(
    host_id: int,
    data: HostUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        clean = {k: v for k, v in data.model_dump().items() if v is not None}
        host = await update_host(db, host_id, clean)
        return HostResponse(**host)
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/hosts/{host_id}", status_code=204)
async def delete_host_endpoint(
    host_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_host(db, host_id)
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/hosts/{host_id}/test", response_model=HostTestResponse)
async def test_host_connection(
    host_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        host = await get_host(db, host_id)
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    reachable = await test_connection(host)
    return HostTestResponse(
        reachable=reachable,
        alias=host["alias"],
        hostname=host["hostname"],
    )


@router.post("/hosts/{host_id}/discover", response_model=dict)
async def discover_host_sessions(
    host_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Discover running agent tmux sessions on a remote host."""
    try:
        host = await get_host(db, host_id)
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    sessions = await discover_remote_agent_sessions(host)
    return {"sessions": sessions, "count": len(sessions)}
