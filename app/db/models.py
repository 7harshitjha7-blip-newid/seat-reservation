import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, ENUM
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


seat_status_enum = ENUM(
    "available",
    "held",
    "confirmed",
    name="seat_status",
    create_type=False,
)


reservation_status_enum = ENUM(
    "confirmed",
    "cancelled",
    "expired",
    name="reservation_status",
    create_type=False,
)


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    price_paise: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    per_user_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Seat(Base):
    __tablename__ = "seats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    show_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shows.id", ondelete="CASCADE"),
        nullable=False,
    )

    seat_number: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        seat_status_enum,
        nullable=False,
        default="available",
    )

    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "seat_number",
            name="seats_show_id_seat_number_key",
        ),
    )


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    show_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shows.id"),
        nullable=False,
    )

    user_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    amount_paise: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        reservation_status_enum,
        nullable=False,
        default="confirmed",
    )

    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "user_id",
            "idempotency_key",
            name="reservations_show_id_user_id_idempotency_key_key",
        ),
    )


class ReservationSeat(Base):
    __tablename__ = "reservation_seats"

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "reservations.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    seat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("seats.id"),
        primary_key=True,
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    show_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shows.id"),
        nullable=False,
    )

    user_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    request_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Your existing DB does NOT have an FK on this column.
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "user_id",
            "idempotency_key",
            name="idempotency_keys_show_id_user_id_idempotency_key_key",
        ),
    )


class UserShowLimit(Base):
    __tablename__ = "user_show_limits"

    show_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shows.id"),
        primary_key=True,
    )

    user_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
    )

    active_seat_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

class RequestRateLimit(Base):
    __tablename__ = "request_rate_limits"

    user_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
    )

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )

    request_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )