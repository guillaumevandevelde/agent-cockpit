"""Codex configuration API."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.codex_config_service import CodexConfigService

router = APIRouter()


class CodexConfigUpdateRequest(BaseModel):
    settings: dict = Field(default_factory=dict)
    features: dict = Field(default_factory=dict)


class CodexConfigResponse(BaseModel):
    provider: str
    path: str
    exists: bool
    parse_error: str | None = None
    summary: dict[str, Any]
    profile_resolution: dict[str, Any] | None = None


class CodexConfigFileEntry(BaseModel):
    path: str
    scope: str
    exists: bool
    content: str | None = None
    provider: str


class CodexConfigFileListResponse(BaseModel):
    files: list[CodexConfigFileEntry]
    count: int


class CodexConfigFileContent(BaseModel):
    path: str
    content: str
    exists: bool
    parse_error: str | None = None


class CodexConfigUpdateResponse(BaseModel):
    success: bool
    path: str
    backup_path: str | None = None
    config: CodexConfigResponse


@router.get("/codex-config", response_model=CodexConfigResponse)
async def get_codex_config():
    return await asyncio.to_thread(CodexConfigService().get_config)


@router.get("/codex-config/files", response_model=CodexConfigFileListResponse)
async def list_codex_config_files():
    service = CodexConfigService()
    files = await asyncio.to_thread(service.get_all_config_files)
    return {"files": files, "count": len(files)}


@router.get("/codex-config/file", response_model=CodexConfigFileContent)
async def get_codex_config_file(path: str):
    try:
        return await asyncio.to_thread(CodexConfigService().get_file_content, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/codex-config/raw", response_model=CodexConfigFileContent)
async def get_codex_config_raw(path: str):
    return await get_codex_config_file(path)


@router.patch("/codex-config", response_model=CodexConfigUpdateResponse)
async def update_codex_config(request: CodexConfigUpdateRequest):
    try:
        return await asyncio.to_thread(
            CodexConfigService().update_safe_settings,
            settings=request.settings,
            features=request.features,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/codex-config", response_model=CodexConfigUpdateResponse)
async def replace_codex_config_safe_settings(request: CodexConfigUpdateRequest):
    return await update_codex_config(request)
