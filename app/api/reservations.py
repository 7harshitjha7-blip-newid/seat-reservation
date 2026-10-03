from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_limit_service import check_reservation_rate_limit
from app.core.security import CurrentUser, get_current_user
from app.db.database import get_db
from app.schemas.reservation import (
    ReserveRequest,
    ReservationResponse,
)
from app.services.reservation_service import (
    cancel_reservation,
    reserve_seats,
)


router = APIRouter(
    tags=["Reservations"],
)


@router.post(
    "/shows/{show_id}/reserve",
    response_model=ReservationResponse,
    status_code=201,
)
async def reserve_show_seats(
    show_id: UUID,
    request: ReserveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):  
    await check_reservation_rate_limit(
        db=db,
        user_id=current_user.user_id,
    )
    
    return await reserve_seats(
        db=db,
        show_id=show_id,
        user_id=current_user.user_id,
        data=request,
    )


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationResponse,
    status_code=200,
)
async def cancel_reservation_endpoint(
    reservation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await cancel_reservation(
        db=db,
        reservation_id=reservation_id,
        user_id=current_user.user_id,
    )