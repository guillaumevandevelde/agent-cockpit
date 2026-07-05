"""Filesystem browsing endpoint for the file picker."""
import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool


class DirectoryListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FileEntry]


@router.get("/files", response_model=DirectoryListing, tags=["Files"])
async def list_directory(path: str = Query(default=str(os.path.expanduser("~")))):
    resolved = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(resolved):
        raise HTTPException(status_code=400, detail="Not a directory")

    try:
        raw = list(os.scandir(resolved))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    entries = sorted(
        [FileEntry(name=e.name, path=e.path, is_dir=e.is_dir(follow_symlinks=False)) for e in raw if not e.name.startswith(".")],
        key=lambda e: (not e.is_dir, e.name.lower()),
    )

    parent = str(os.path.dirname(resolved)) if resolved != os.path.dirname(resolved) else None
    return DirectoryListing(path=resolved, parent=parent, entries=entries)
