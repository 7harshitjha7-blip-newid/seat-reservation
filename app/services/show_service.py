from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Seat, Show
from app.schemas.show import CreateShowRequest


async def create_show(
    db: AsyncSession,
    data: CreateShowRequest,
) -> dict:

    normalized_seats = [seat.strip().upper() for seat in data.seats]

    if any(not seat for seat in normalized_seats):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seat names cannot be empty",
        )

    if len(normalized_seats) != len(set(normalized_seats)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate seat names are not allowed",
        )

    show = Show(
        id=uuid4(),
        name=data.name.strip(),
        price_paise=data.price_paise,
        per_user_limit=data.per_user_limit,
    )

    db.add(show)
    await db.flush()

    seats = [
        Seat(
            id=uuid4(),
            show_id=show.id,
            seat_number=seat_number,
            status="available",
        )
        for seat_number in normalized_seats
    ]

    db.add_all(seats)

    await db.commit()

    return {
        "id": show.id,
        "name": show.name,
        "price_paise": show.price_paise,
        "per_user_limit": show.per_user_limit,
        "seats": [
            {
                "seat_number": seat.seat_number,
                "status": seat.status,
            }
            for seat in seats
        ],
    }

async def get_show(
    db: AsyncSession,
    show_id: UUID,
) -> dict:

    # Fetch show
    show_result = await db.execute(
        select(Show).where(
            Show.id == show_id
        )
    )

    show = show_result.scalar_one_or_none()

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Show not found",
        )

    # Fetch seats
    seats_result = await db.execute(
        select(Seat)
        .where(Seat.show_id == show_id)
        .order_by(Seat.seat_number)
    )

    seats = seats_result.scalars().all()

    # Calculate counts from the source of truth
    summary_result = await db.execute(
        select(
            func.count(Seat.id).label("total"),

            func.count(Seat.id)
            .filter(Seat.status == "available")
            .label("available"),

            func.count(Seat.id)
            .filter(Seat.status == "held")
            .label("held"),

            func.count(Seat.id)
            .filter(Seat.status == "confirmed")
            .label("confirmed"),
        )
        .where(Seat.show_id == show_id)
    )

    summary = summary_result.one()

    total = summary.total
    available = summary.available
    held = summary.held
    confirmed = summary.confirmed

    # Reconciliation invariant
    if available + held + confirmed != total:
        raise RuntimeError(
            "Seat reconciliation invariant violated"
        )

    return {
        "id": show.id,
        "name": show.name,
        "price_paise": show.price_paise,
        "per_user_limit": show.per_user_limit,

        "summary": {
            "total": total,
            "available": available,
            "held": held,
            "confirmed": confirmed,
        },

        "seats": [
            {
                "seat_number": seat.seat_number,
                "status": seat.status,
            }
            for seat in seats
        ],
    }