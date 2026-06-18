"""Pydantic schemas for autonomy profiles."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


AutonomyMode = Literal["plan", "suggest", "auto"]


class AutonomyProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: AutonomyMode = "suggest"
    description: Optional[str] = Field(default=None, max_length=512)
    is_default: bool = False
    allowed_tools: Optional[list[str]] = None
    denied_tools: Optional[list[str]] = None
    max_file_size_kb: Optional[int] = Field(default=None, ge=1)
    require_approval_for: Optional[list[str]] = None


class AutonomyProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    mode: Optional[AutonomyMode] = None
    description: Optional[str] = Field(default=None, max_length=512)
    is_default: Optional[bool] = None
    allowed_tools: Optional[list[str]] = None
    denied_tools: Optional[list[str]] = None
    max_file_size_kb: Optional[int] = Field(default=None, ge=1)
    require_approval_for: Optional[list[str]] = None


class AutonomyProfileResponse(BaseModel):
    id: int
    name: str
    mode: AutonomyMode
    description: Optional[str] = None
    is_default: bool
    allowed_tools: Optional[list[str]] = None
    denied_tools: Optional[list[str]] = None
    max_file_size_kb: Optional[int] = None
    require_approval_for: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ActiveAutonomy(BaseModel):
    """The currently active autonomy mode for the UI toggle."""
    mode: AutonomyMode
    profile_name: str
    description: Optional[str] = None


class ActiveAutonomyUpdate(BaseModel):
    mode: AutonomyMode
