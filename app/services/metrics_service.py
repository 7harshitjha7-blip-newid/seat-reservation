from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import seats_available
from app.db.models import Seat


async def reconcile_seats_available(
    db: AsyncSession,
) -> int:
    result = await db.execute(
        select(func.count(Seat.id)).where(
            Seat.status == "available"
        )
    )

    available_count = result.scalar_one()

    seats_available.set(available_count)

    return available_count