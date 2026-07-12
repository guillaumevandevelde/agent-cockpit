"""Read-only portfolio overview API."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.kanban import db as kanban_db
from app.services.portfolio_service import PortfolioOverview, PortfolioService

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/overview", response_model=PortfolioOverview)
async def portfolio_overview(db: AsyncSession = Depends(get_db)) -> PortfolioOverview:
    """Aggregate every project's kanban stats into one read-only overview."""
    async with kanban_db.KanbanSessionLocal() as kanban:
        return await PortfolioService(db, kanban).aggregate()
