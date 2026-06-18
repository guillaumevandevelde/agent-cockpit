"""REST API for automation templates."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.automation_template import AutomationTemplate
from app.models.automation_template_schemas import (
    AutomationTemplateCreate,
    AutomationTemplateUpdate,
    AutomationTemplateResponse,
)

router = APIRouter(prefix="/automation-templates", tags=["Automation Templates"])

BUILTIN_TEMPLATES = [
    {
        "name": "Daily Code Review",
        "description": "Run automated code review on recent changes each morning",
        "category": "review",
        "icon": "eye",
        "trigger_type": "cron",
        "cron_expr": "0 9 * * 1-5",
        "message_template": "Review all uncommitted changes and recent commits from today. Check for code quality issues, potential bugs, and suggest improvements. Summarize findings.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["review", "daily", "quality"],
    },
    {
        "name": "Dependency Update Check",
        "description": "Weekly check for outdated dependencies and security vulnerabilities",
        "category": "monitor",
        "icon": "shield",
        "trigger_type": "cron",
        "cron_expr": "0 10 * * 1",
        "message_template": "Check all project dependencies for available updates. Identify any security vulnerabilities. Create a summary of what should be updated and why.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["dependencies", "security", "weekly"],
    },
    {
        "name": "Test Suite Runner",
        "description": "Run full test suite and report results",
        "category": "quality",
        "icon": "check-circle",
        "trigger_type": "cron",
        "cron_expr": "0 */4 * * *",
        "message_template": "Run the complete test suite. Report pass/fail counts, any new failures, and coverage changes. If all tests pass, confirm green status.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["tests", "ci", "quality"],
    },
    {
        "name": "PR Review Assistant",
        "description": "Review a pull request and provide detailed feedback",
        "category": "review",
        "icon": "git-pull-request",
        "trigger_type": "once",
        "message_template": "Review the current pull request. Check for code quality, test coverage, documentation, and potential issues. Provide constructive feedback with specific suggestions.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["pr", "review", "code-quality"],
    },
    {
        "name": "Documentation Sync",
        "description": "Ensure documentation matches current code state",
        "category": "quality",
        "icon": "book-open",
        "trigger_type": "cron",
        "cron_expr": "0 14 * * 5",
        "message_template": "Review all documentation files (README, docs/, comments) and check if they accurately reflect the current codebase. Update outdated sections and flag missing documentation.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["documentation", "weekly"],
    },
    {
        "name": "Performance Audit",
        "description": "Analyze code for performance bottlenecks",
        "category": "quality",
        "icon": "gauge",
        "trigger_type": "once",
        "message_template": "Analyze the codebase for performance bottlenecks. Look for inefficient loops, unnecessary re-renders, memory leaks, N+1 queries, and missing caching opportunities. Provide specific optimization suggestions.",
        "target_projects": None,
        "permission_mode": "suggest",
        "is_builtin": True,
        "tags": ["performance", "optimization"],
    },
]


@router.get("", response_model=list[AutomationTemplateResponse])
async def list_templates():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(AutomationTemplate).order_by(AutomationTemplate.name)
        )).scalars().all()
        return rows


@router.post("", response_model=AutomationTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(payload: AutomationTemplateCreate):
    async with AsyncSessionLocal() as s:
        existing = (await s.execute(
            select(AutomationTemplate).where(AutomationTemplate.name == payload.name)
        )).scalars().first()
        if existing:
            raise HTTPException(409, f"Template '{payload.name}' already exists")
        template = AutomationTemplate(**payload.model_dump())
        s.add(template)
        await s.commit()
        await s.refresh(template)
        return template


@router.get("/{template_id}", response_model=AutomationTemplateResponse)
async def get_template(template_id: int):
    async with AsyncSessionLocal() as s:
        template = await s.get(AutomationTemplate, template_id)
        if not template:
            raise HTTPException(404, "Template not found")
        return template


@router.patch("/{template_id}", response_model=AutomationTemplateResponse)
async def update_template(template_id: int, payload: AutomationTemplateUpdate):
    async with AsyncSessionLocal() as s:
        template = await s.get(AutomationTemplate, template_id)
        if not template:
            raise HTTPException(404, "Template not found")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(template, k, v)
        await s.commit()
        await s.refresh(template)
        return template


@router.delete("/{template_id}")
async def delete_template(template_id: int):
    async with AsyncSessionLocal() as s:
        template = await s.get(AutomationTemplate, template_id)
        if not template:
            raise HTTPException(404, "Template not found")
        if template.is_builtin:
            raise HTTPException(400, "Cannot delete built-in templates")
        await s.delete(template)
        await s.commit()
        return {"deleted": True}


@router.post("/seed")
async def seed_builtin_templates():
    """Seed built-in templates if not already present."""
    seeded = 0
    async with AsyncSessionLocal() as s:
        for tpl_data in BUILTIN_TEMPLATES:
            existing = (await s.execute(
                select(AutomationTemplate).where(AutomationTemplate.name == tpl_data["name"])
            )).scalars().first()
            if not existing:
                template = AutomationTemplate(**tpl_data)
                s.add(template)
                seeded += 1
        await s.commit()
    return {"seeded": seeded, "total": len(BUILTIN_TEMPLATES)}
