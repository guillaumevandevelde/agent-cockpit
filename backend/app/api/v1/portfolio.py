"""Read-only portfolio overview API."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.kanban import db as kanban_db
from app.services.portfolio_migration import MigrationCandidate, run_migration_pass
from app.services.portfolio_service import PortfolioOverview, PortfolioService

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/overview", response_model=PortfolioOverview)
async def portfolio_overview(db: AsyncSession = Depends(get_db)) -> PortfolioOverview:
    """Aggregate every project's kanban stats into one read-only overview."""
    async with kanban_db.KanbanSessionLocal() as kanban:
        return await PortfolioService(db, kanban).aggregate()


@router.post("/migration-pass", response_model=list[MigrationCandidate])
async def portfolio_migration_pass(
    db: AsyncSession = Depends(get_db),
) -> list[MigrationCandidate]:
    """Run the read-only meta-vs-product classification pass.

    Derives ``kind=meta`` per project via the live cockpit key +
    ``COCKPIT_META_PROJECT_KEYS`` override, posts one idempotent
    ``[portfolio-migration]`` audit-comment per candidate, and returns the
    candidate list. Writes no ``projects.kind`` — a human flips via PATCH.
    """
    async with kanban_db.KanbanSessionLocal() as kanban:
        return await run_migration_pass(db, kanban)
