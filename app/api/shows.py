from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import CurrentUser, require_admin, get_current_user

from app.db.database import get_db
from app.schemas.show import (
    CreateShowRequest,
    ShowDetailResponse,
    ShowResponse,
)
from app.services.show_service import (
    create_show,
    get_show,
)


router = APIRouter(
    prefix="/shows",
    tags=["Shows"],
)


@router.post(
    "",
    response_model=ShowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_show_endpoint(
    request: CreateShowRequest,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(require_admin),
):
    return await create_show(
        db=db,
        data=request,
    )


@router.get(
    "/{show_id}",
    response_model=ShowDetailResponse,
)
async def get_show_endpoint(
    show_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    return await get_show(
        db=db,
        show_id=show_id,
    )