from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seats: list[str] = Field(
        min_length=1,
        max_length=100,
    )

    idempotency_key: str = Field(
        min_length=1,
        max_length=200,
    )


class ReservedSeatResponse(BaseModel):
    seat_number: str


class ReservationResponse(BaseModel):
    reservation_id: UUID
    show_id: UUID
    user_id: str
    seats: list[ReservedSeatResponse]
    amount_paise: int
    status: str