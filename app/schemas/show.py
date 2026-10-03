from uuid import UUID

from pydantic import BaseModel, Field


class CreateShowRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=200,
    )

    seats: list[str] = Field(
        min_length=1,
        max_length=10_000,
    )

    price_paise: int = Field(
        ge=0,
    )

    per_user_limit: int = Field(
        default=4,
        ge=1,
        le=100,
    )


class SeatResponse(BaseModel):
    seat_number: str
    status: str


class ShowResponse(BaseModel):
    id: UUID
    name: str
    price_paise: int
    per_user_limit: int
    seats: list[SeatResponse]


class ShowSummary(BaseModel):
    total: int
    available: int
    held: int
    confirmed: int


class ShowDetailResponse(BaseModel):
    id: UUID
    name: str
    price_paise: int
    per_user_limit: int
    summary: ShowSummary
    seats: list[SeatResponse]