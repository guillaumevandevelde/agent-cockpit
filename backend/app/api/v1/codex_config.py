"""Codex configuration API."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.codex_config_service import CodexConfigService

router = APIRouter()


class CodexConfigUpdateRequest(BaseModel):
    settings: dict = Field(default_factory=dict)
    features: dict = Field(default_factory=dict)


@router.get("/codex-config")
async def get_codex_config():
    return await asyncio.to_thread(CodexConfigService().get_config)


@router.get("/codex-config/files")
async def list_codex_config_files():
    service = CodexConfigService()
    files = await asyncio.to_thread(service.get_all_config_files)
    return {"files": files, "count": len(files)}


@router.get("/codex-config/file")
async def get_codex_config_file(path: str):
    try:
        return await asyncio.to_thread(CodexConfigService().get_file_content, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/codex-config/raw")
async def get_codex_config_raw(path: str):
    return await get_codex_config_file(path)


@router.patch("/codex-config")
async def update_codex_config(request: CodexConfigUpdateRequest):
    try:
        return await asyncio.to_thread(
            CodexConfigService().update_safe_settings,
            settings=request.settings,
            features=request.features,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/codex-config")
async def replace_codex_config_safe_settings(request: CodexConfigUpdateRequest):
    return await update_codex_config(request)
