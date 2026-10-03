from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db


router = APIRouter(tags=["Health"])


@router.get("/health/live")
async def liveness():
    return {
        "status": "ok"
    }


@router.get("/health/ready")
async def readiness(
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("SELECT 1"))

    return {
        "status": "ready",
        "database": "ok",
    }