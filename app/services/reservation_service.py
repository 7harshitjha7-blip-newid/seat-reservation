import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import (
    reservations_confirmed_total,
    reservations_declined_total,
    reservations_cancelled_total,
    reservation_request_duration_seconds,
)

from app.services.metrics_service import reconcile_seats_available

from app.core.logging import event_logger

from app.db.models import (
    IdempotencyKey,
    Reservation,
    ReservationSeat,
    Seat,
    Show,
    UserShowLimit,
)
from app.schemas.reservation import (
    ReserveRequest,
    ReservationResponse,
    ReservedSeatResponse,
)


def normalize_seats(seats: list[str]) -> list[str]:
    normalized = [
        seat.strip().upper()
        for seat in seats
    ]

    if any(not seat for seat in normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seat names cannot be empty",
        )

    if len(normalized) != len(set(normalized)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate seat names are not allowed",
        )

    # Deterministic ordering is important for concurrency.
    return sorted(normalized)


def build_request_hash(seats: list[str]) -> str:
    canonical_request = json.dumps(
        {
            "seats": seats,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()


async def build_reservation_response(
    db: AsyncSession,
    reservation: Reservation,
) -> ReservationResponse:

    result = await db.execute(
        select(Seat)
        .join(
            ReservationSeat,
            ReservationSeat.seat_id == Seat.id,
        )
        .where(
            ReservationSeat.reservation_id == reservation.id
        )
        .order_by(Seat.seat_number)
    )

    seats = result.scalars().all()

    return ReservationResponse(
        reservation_id=reservation.id,
        show_id=reservation.show_id,
        user_id=reservation.user_id,
        seats=[
            ReservedSeatResponse(
                seat_number=seat.seat_number
            )
            for seat in seats
        ],
        amount_paise=reservation.amount_paise,
        status=reservation.status,
    )


async def reserve_seats(
    db: AsyncSession,
    show_id: UUID,
    user_id: str,
    data: ReserveRequest,
) -> ReservationResponse:

    with reservation_request_duration_seconds.time():

        requested_seats = normalize_seats(data.seats)

        request_hash = build_request_hash(
            requested_seats
        )

        async with db.begin():

            # --------------------------------------------------
            # 1. Verify show exists
            # --------------------------------------------------

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

            # --------------------------------------------------
            # 2. Create user/show limit row if necessary
            # --------------------------------------------------

            await db.execute(
                insert(UserShowLimit)
                .values(
                    show_id=show_id,
                    user_id=user_id,
                    active_seat_count=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        UserShowLimit.show_id,
                        UserShowLimit.user_id,
                    ]
                )
            )

            # --------------------------------------------------
            # 3. LOCK user/show limit row
            #
            # This serializes concurrent reservations from
            # the same user for the same show.
            # --------------------------------------------------

            limit_result = await db.execute(
                select(UserShowLimit)
                .where(
                    UserShowLimit.show_id == show_id,
                    UserShowLimit.user_id == user_id,
                )
                .with_for_update()
            )

            user_limit = limit_result.scalar_one()

            # --------------------------------------------------
            # 4. Idempotency
            # --------------------------------------------------

            await db.execute(
                insert(IdempotencyKey)
                .values(
                    id=uuid4(),
                    show_id=show_id,
                    user_id=user_id,
                    idempotency_key=data.idempotency_key,
                    request_hash=request_hash,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        IdempotencyKey.show_id,
                        IdempotencyKey.user_id,
                        IdempotencyKey.idempotency_key,
                    ]
                )
            )

            idempotency_result = await db.execute(
                select(IdempotencyKey)
                .where(
                    IdempotencyKey.show_id == show_id,
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.idempotency_key
                    == data.idempotency_key,
                )
                .with_for_update()
            )

            idempotency_record = (
                idempotency_result.scalar_one()
            )

            # Same idempotency key but different request.
            if (
                idempotency_record.request_hash
                != request_hash
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Idempotency key was already used "
                        "with a different request"
                    ),
                )

            # Same request has already completed.
            if idempotency_record.reservation_id is not None:

                reservation_result = await db.execute(
                    select(Reservation).where(
                        Reservation.id
                        == idempotency_record.reservation_id
                    )
                )

                existing_reservation = (
                    reservation_result.scalar_one()
                )
                # timer.observe_duration()

                return await build_reservation_response(
                    db,
                    existing_reservation,
                )

            # --------------------------------------------------
            # 5. Per-user seat limit
            # --------------------------------------------------

            requested_count = len(requested_seats)

            if (
                user_limit.active_seat_count
                + requested_count
                > show.per_user_limit
            ):
                reservations_declined_total.labels(
                    reason="user_limit"
                ).inc()

                event_logger.info(
                    "reservation_declined",
                    extra={
                        "event": "reservation_declined",
                        "user_id": user_id,
                        "show_id": str(show_id),
                        "reason": "user_limit",
                        "seat_count": requested_count,
                    },
                )

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Per-user seat limit exceeded. "
                        f"Limit is {show.per_user_limit} seats."
                    ),
                )

            # --------------------------------------------------
            # 6. LOCK requested seats
            #
            # requested_seats is already sorted, giving every
            # transaction the same lock order.
            # --------------------------------------------------

            seats_result = await db.execute(
                select(Seat)
                .where(
                    Seat.show_id == show_id,
                    Seat.seat_number.in_(requested_seats),
                )
                .order_by(Seat.seat_number)
                .with_for_update()
            )

            seats = seats_result.scalars().all()

            # --------------------------------------------------
            # 7. Seat existence check
            # --------------------------------------------------

            if len(seats) != len(requested_seats):

                found_seats = {
                    seat.seat_number
                    for seat in seats
                }

                missing_seats = [
                    seat
                    for seat in requested_seats
                    if seat not in found_seats
                ]

                reservations_declined_total.labels(
                    reason="invalid_seat"
                ).inc()

                event_logger.info(
                    "reservation_declined",
                    extra={
                        "event": "reservation_declined",
                        "user_id": user_id,
                        "show_id": str(show_id),
                        "reason": "invalid_seat",
                        "seat_count": len(requested_seats),
                    },
                )

                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "message": "One or more seats do not exist",
                        "seats": missing_seats,
                    },
                )

            # --------------------------------------------------
            # 8. Availability check
            # --------------------------------------------------

            unavailable_seats = [
                seat.seat_number
                for seat in seats
                if seat.status != "available"
            ]

            if unavailable_seats:
                reservations_declined_total.labels(
                    reason="seat_unavailable"
                ).inc()

                event_logger.info(
                    "reservation_declined",
                    extra={
                        "event": "reservation_declined",
                        "user_id": user_id,
                        "show_id": str(show_id),
                        "reason": "seat_unavailable",
                        "seat_count": len(unavailable_seats),
                    },
                )

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "One or more seats are unavailable",
                        "seats": unavailable_seats,
                    },
                )

            # --------------------------------------------------
            # 9. Create reservation
            # --------------------------------------------------

            reservation = Reservation(
                id=uuid4(),
                show_id=show_id,
                user_id=user_id,
                amount_paise=(
                    show.price_paise
                    * requested_count
                ),
                status="confirmed",
                idempotency_key=data.idempotency_key,
            )

            db.add(reservation)

            await db.flush()

            # --------------------------------------------------
            # 10. Create reservation-seat records
            # --------------------------------------------------

            db.add_all(
                [
                    ReservationSeat(
                        reservation_id=reservation.id,
                        seat_id=seat.id,
                    )
                    for seat in seats
                ]
            )

            # --------------------------------------------------
            # 11. Mark seats confirmed
            # --------------------------------------------------

            await db.execute(
                update(Seat)
                .where(
                    Seat.id.in_(
                        [seat.id for seat in seats]
                    )
                )
                .values(
                    status="confirmed",
                    reservation_id=reservation.id,
                )
            )

            # --------------------------------------------------
            # 12. Update user's active seat count
            # --------------------------------------------------

            user_limit.active_seat_count += requested_count

            # --------------------------------------------------
            # 13. Complete idempotency record
            # --------------------------------------------------

            idempotency_record.reservation_id = (
                reservation.id
            )

        await reconcile_seats_available(db)            
        # --------------------------------------------------
        # 14. Transaction commits automatically
        # --------------------------------------------------

        event_logger.info(
            "reservation_confirmed",
            extra={
                "event": "reservation_confirmed",
                "user_id": user_id,
                "show_id": str(show_id),
                "reservation_id": str(reservation.id),
                "seat_count": requested_count,
                "amount_paise": reservation.amount_paise,
            },
        )
        reservations_confirmed_total.inc()
        return await build_reservation_response(
            db,
            reservation,
        )

async def cancel_reservation(
    db: AsyncSession,
    reservation_id: UUID,
    user_id: str,
):
    async with db.begin():

        reservation_result = await db.execute(
            select(Reservation).where(
                Reservation.id == reservation_id
            )
        )

        reservation_snapshot = reservation_result.scalar_one_or_none()

        if reservation_snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="Reservation not found",
            )

        if reservation_snapshot.user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail="You can only cancel your own reservation",
            )

        show_id = reservation_snapshot.show_id

        # ---------------------------------------------------------
        # Lock user/show limit first.
        # This preserves the same lock ordering as reservation flow.
        # ---------------------------------------------------------
        limit_result = await db.execute(
            select(UserShowLimit)
            .where(
                UserShowLimit.show_id == show_id,
                UserShowLimit.user_id == user_id,
            )
            .with_for_update()
        )

        user_limit = limit_result.scalar_one_or_none()

        if user_limit is None:
            raise HTTPException(
                status_code=409,
                detail="User reservation limit record not found",
            )

        # ---------------------------------------------------------
        # Lock the reservation.
        # ---------------------------------------------------------
        reservation_result = await db.execute(
            select(Reservation)
            .where(
                Reservation.id == reservation_id,
                Reservation.user_id == user_id,
            )
            .with_for_update()
        )

        reservation = reservation_result.scalar_one_or_none()

        if reservation is None:
            raise HTTPException(
                status_code=404,
                detail="Reservation not found",
            )

        # ---------------------------------------------------------
        # Idempotent cancellation.
        # ---------------------------------------------------------
        if reservation.status == "cancelled":
            return await build_reservation_response(
                db,
                reservation,
            )

        if reservation.status != "confirmed":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Reservation cannot be cancelled "
                    f"because its status is '{reservation.status}'"
                ),
            )

        # ---------------------------------------------------------
        # Lock all seats belonging to this reservation.
        # Ordered locking keeps multi-seat operations deterministic.
        # ---------------------------------------------------------
        seats_result = await db.execute(
            select(Seat)
            .join(
                ReservationSeat,
                ReservationSeat.seat_id == Seat.id,
            )
            .where(
                ReservationSeat.reservation_id == reservation.id,
                Seat.show_id == reservation.show_id,
            )
            .order_by(Seat.seat_number)
            .with_for_update()
        )

        seats = seats_result.scalars().all()

        if not seats:
            raise HTTPException(
                status_code=409,
                detail="Reservation has no seats",
            )

        # ---------------------------------------------------------
        # Validate the seat state before releasing.
        # ---------------------------------------------------------
        invalid_seats = [
            seat.seat_number
            for seat in seats
            if (
                seat.status != "confirmed"
                or seat.reservation_id != reservation.id
            )
        ]

        if invalid_seats:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Reservation seat state is inconsistent",
                    "seats": invalid_seats,
                },
            )

        seat_count = len(seats)

        # ---------------------------------------------------------
        # Validate user-show counter.
        # ---------------------------------------------------------
        if user_limit.active_seat_count < seat_count:
            raise HTTPException(
                status_code=409,
                detail="User reservation limit state is inconsistent",
            )

        seat_ids = [seat.id for seat in seats]

        # ---------------------------------------------------------
        # IMPORTANT:
        # Remove the reservation_seats ownership rows.
        #
        # reservation_seats has UNIQUE(seat_id), so these rows must
        # be deleted before another reservation can claim the seats.
        # ---------------------------------------------------------
        await db.execute(
            delete(ReservationSeat).where(
                ReservationSeat.reservation_id == reservation.id,
                ReservationSeat.seat_id.in_(seat_ids),
            )
        )

        # ---------------------------------------------------------
        # Release the actual seat rows.
        # ---------------------------------------------------------
        await db.execute(
            update(Seat)
            .where(
                Seat.id.in_(seat_ids)
            )
            .values(
                status="available",
                reservation_id=None,
            )
        )

        # ---------------------------------------------------------
        # Decrease active seat count.
        # ---------------------------------------------------------
        user_limit.active_seat_count -= seat_count

        # ---------------------------------------------------------
        # Preserve reservation history.
        # ---------------------------------------------------------
        reservation.status = "cancelled"

    await reconcile_seats_available(db)

    reservations_cancelled_total.inc()

    event_logger.info(
        "reservation_cancelled",
        extra={
            "event": "reservation_cancelled",
            "user_id": user_id,
            "show_id": str(show_id),
            "reservation_id": str(reservation.id),
            "seat_count": seat_count,
        },
    )

    return await build_reservation_response(
        db,
        reservation,
    )