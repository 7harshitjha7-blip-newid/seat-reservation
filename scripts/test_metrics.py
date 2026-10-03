import sys
import uuid

import requests
import jwt

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"

PER_USER_LIMIT = 4


def generate_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "role": "user",
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def headers(user_id: str) -> dict:
    return {
        "Authorization": f"Bearer {generate_token(user_id)}",
        "Content-Type": "application/json",
    }


def get_metrics() -> str:
    response = requests.get(
        f"{BASE_URL}/metrics",
        timeout=10,
    )

    response.raise_for_status()

    return response.text


def get_show(show_id: str) -> dict:
    response = requests.get(
        f"{BASE_URL}/shows/{show_id}",
        headers=headers(f"metrics-read-{uuid.uuid4()}"),
        timeout=10,
    )

    if response.status_code != 200:
        print(response.text)
        raise RuntimeError(
            f"Could not read show. HTTP {response.status_code}"
        )

    return response.json()


def metric_value(
    metrics: str,
    metric_name: str,
    labels: str | None = None,
) -> float:
    target = metric_name

    if labels:
        target += labels

    for line in metrics.splitlines():
        if line.startswith(target + " "):
            return float(line.split()[-1])

    return 0.0


def histogram_count(
    metrics: str,
    metric_name: str,
) -> float:
    target = f"{metric_name}_count"

    for line in metrics.splitlines():
        if line.startswith(target + " "):
            return float(line.split()[-1])

    return 0.0


def print_metric(
    metrics: str,
    metric_name: str,
    labels: str | None = None,
):
    value = metric_value(
        metrics,
        metric_name,
        labels,
    )

    if labels:
        print(
            f"{metric_name}{labels} = {value}"
        )
    else:
        print(
            f"{metric_name} = {value}"
        )

    return value


def reserve(
    show_id: str,
    user_id: str,
    seat: str,
    idempotency_key: str,
):
    return requests.post(
        f"{BASE_URL}/shows/{show_id}/reserve",
        headers=headers(user_id),
        json={
            "seats": [seat],
            "idempotency_key": idempotency_key,
        },
        timeout=10,
    )


def cancel(
    reservation_id: str,
    user_id: str,
):
    return requests.post(
        f"{BASE_URL}/reservations/{reservation_id}/cancel",
        headers=headers(user_id),
        timeout=10,
    )


def get_available_seats(show_id: str, count: int) -> list[str]:
    show = get_show(show_id)

    available = [
        seat["seat_number"]
        for seat in show["seats"]
        if seat["status"] == "available"
    ]

    if len(available) < count:
        raise RuntimeError(
            f"Need {count} available seats, "
            f"but only found {len(available)}"
        )

    return available[:count]


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python -m scripts.test_metrics SHOW_ID"
        )
        sys.exit(1)

    show_id = sys.argv[1]

    print("=" * 70)
    print("METRICS TEST")
    print("=" * 70)

    # --------------------------------------------------
    # 1. Initial metrics
    # --------------------------------------------------

    print("\n[1] Reading initial metrics...")

    metrics_before = get_metrics()

    confirmed_before = metric_value(
        metrics_before,
        "reservations_confirmed_total",
    )

    cancelled_before = metric_value(
        metrics_before,
        "reservations_cancelled_total",
    )

    unavailable_before = metric_value(
        metrics_before,
        "reservations_declined_total",
        '{reason="seat_unavailable"}',
    )

    invalid_before = metric_value(
        metrics_before,
        "reservations_declined_total",
        '{reason="invalid_seat"}',
    )

    user_limit_before = metric_value(
        metrics_before,
        "reservations_declined_total",
        '{reason="user_limit"}',
    )

    rate_limit_before = metric_value(
        metrics_before,
        "rate_limit_rejections_total",
    )

    histogram_before = histogram_count(
        metrics_before,
        "reservation_request_duration_seconds",
    )

    seats_available_before = metric_value(
        metrics_before,
        "seats_available",
    )

    print_metric(
        metrics_before,
        "reservations_confirmed_total",
    )

    print_metric(
        metrics_before,
        "reservations_cancelled_total",
    )

    print_metric(
        metrics_before,
        "reservations_declined_total",
        '{reason="seat_unavailable"}',
    )

    print_metric(
        metrics_before,
        "reservations_declined_total",
        '{reason="invalid_seat"}',
    )

    print_metric(
        metrics_before,
        "reservations_declined_total",
        '{reason="user_limit"}',
    )

    print_metric(
        metrics_before,
        "rate_limit_rejections_total",
    )

    print(
        "reservation_request_duration_seconds_count"
        f" = {histogram_before}"
    )

    print_metric(
        metrics_before,
        "seats_available",
    )

    # --------------------------------------------------
    # Get available seats
    # --------------------------------------------------

    available_seats = get_available_seats(
        show_id,
        10,
    )

    print(
        f"\nAvailable test seats: {available_seats}"
    )

    created_reservations = []

    # --------------------------------------------------
    # 2. Successful reservation
    # --------------------------------------------------

    print("\n[2] Testing successful reservation...")

    user_id = f"metrics-user-{uuid.uuid4()}"
    idempotency_key = f"metrics-success-{uuid.uuid4()}"
    test_seat = available_seats[0]

    response = reserve(
        show_id=show_id,
        user_id=user_id,
        seat=test_seat,
        idempotency_key=idempotency_key,
    )

    print(
        f"Reservation response: {response.status_code}"
    )

    if response.status_code != 201:
        print(response.text)
        raise RuntimeError(
            "Expected successful reservation (201)"
        )

    reservation = response.json()
    reservation_id = reservation["reservation_id"]

    created_reservations.append(
        (reservation_id, user_id)
    )

    print(
        f"Reservation created: {reservation_id}"
    )

    metrics_after_reservation = get_metrics()

    confirmed_after = metric_value(
        metrics_after_reservation,
        "reservations_confirmed_total",
    )

    if confirmed_after != confirmed_before + 1:
        raise RuntimeError(
            "reservations_confirmed_total "
            "did not increase by 1"
        )

    print(
        "PASS: reservations_confirmed_total increased"
    )

    # --------------------------------------------------
    # 3. Cancellation
    # --------------------------------------------------

    print("\n[3] Testing cancellation...")

    response = cancel(
        reservation_id=reservation_id,
        user_id=user_id,
    )

    print(
        f"Cancellation response: {response.status_code}"
    )

    if response.status_code != 200:
        print(response.text)
        raise RuntimeError(
            "Expected cancellation to succeed (200)"
        )

    created_reservations.remove(
        (reservation_id, user_id)
    )

    metrics_after_cancel = get_metrics()

    cancelled_after = metric_value(
        metrics_after_cancel,
        "reservations_cancelled_total",
    )

    if cancelled_after != cancelled_before + 1:
        raise RuntimeError(
            "reservations_cancelled_total "
            "did not increase by 1"
        )

    print(
        "PASS: reservations_cancelled_total increased"
    )

    # --------------------------------------------------
    # 4. Seat unavailable
    # --------------------------------------------------

    print(
        "\n[4] Testing seat_unavailable metric..."
    )

    blocker_user = f"metrics-blocker-{uuid.uuid4()}"
    blocker_seat = available_seats[1]

    response = reserve(
        show_id=show_id,
        user_id=blocker_user,
        seat=blocker_seat,
        idempotency_key=f"metrics-blocker-{uuid.uuid4()}",
    )

    if response.status_code != 201:
        print(response.text)
        raise RuntimeError(
            "Could not create blocker reservation"
        )

    blocker_reservation_id = response.json()[
        "reservation_id"
    ]

    created_reservations.append(
        (
            blocker_reservation_id,
            blocker_user,
        )
    )

    metrics_before_unavailable = get_metrics()

    response = reserve(
        show_id=show_id,
        user_id=f"metrics-contender-{uuid.uuid4()}",
        seat=blocker_seat,
        idempotency_key=f"metrics-unavailable-{uuid.uuid4()}",
    )

    print(
        f"Unavailable seat response: "
        f"{response.status_code}"
    )

    if response.status_code != 409:
        print(response.text)
        raise RuntimeError(
            "Expected 409 for unavailable seat"
        )

    metrics_after_unavailable = get_metrics()

    unavailable_after = metric_value(
        metrics_after_unavailable,
        "reservations_declined_total",
        '{reason="seat_unavailable"}',
    )

    unavailable_before_test = metric_value(
        metrics_before_unavailable,
        "reservations_declined_total",
        '{reason="seat_unavailable"}',
    )

    if unavailable_after != unavailable_before_test + 1:
        raise RuntimeError(
            "seat_unavailable metric "
            "did not increase"
        )

    print(
        "PASS: seat_unavailable metric increased"
    )

    # --------------------------------------------------
    # 5. Invalid seat
    # --------------------------------------------------

    print("\n[5] Testing invalid_seat metric...")

    metrics_before_invalid = get_metrics()

    response = reserve(
        show_id=show_id,
        user_id=f"metrics-invalid-{uuid.uuid4()}",
        seat="THIS-SEAT-DOES-NOT-EXIST",
        idempotency_key=f"metrics-invalid-{uuid.uuid4()}",
    )

    print(
        f"Invalid seat response: "
        f"{response.status_code}"
    )

    if response.status_code != 404:
        print(response.text)
        raise RuntimeError(
            "Expected 404 for invalid seat"
        )

    metrics_after_invalid = get_metrics()

    invalid_after = metric_value(
        metrics_after_invalid,
        "reservations_declined_total",
        '{reason="invalid_seat"}',
    )

    invalid_before_test = metric_value(
        metrics_before_invalid,
        "reservations_declined_total",
        '{reason="invalid_seat"}',
    )

    if invalid_after != invalid_before_test + 1:
        raise RuntimeError(
            "invalid_seat metric "
            "did not increase"
        )

    print(
        "PASS: invalid_seat metric increased"
    )

    # --------------------------------------------------
    # 6. User limit
    # --------------------------------------------------

    print("\n[6] Testing user_limit metric...")

    limit_user = f"metrics-limit-{uuid.uuid4()}"

    limit_seats = available_seats[2:6]

    limit_reservation_ids = []

    for index, seat in enumerate(limit_seats):
        response = reserve(
            show_id=show_id,
            user_id=limit_user,
            seat=seat,
            idempotency_key=(
                f"metrics-limit-{index}-{uuid.uuid4()}"
            ),
        )

        print(
            f"Limit reservation {index + 1}: "
            f"{response.status_code}"
        )

        if response.status_code != 201:
            print(response.text)
            raise RuntimeError(
                "Expected reservation to succeed "
                f"before reaching limit: {response.status_code}"
            )

        reservation_id = response.json()["reservation_id"]

        limit_reservation_ids.append(
            reservation_id
        )

        created_reservations.append(
            (
                reservation_id,
                limit_user,
            )
        )

    metrics_before_limit = get_metrics()

    fifth_seat = available_seats[6]

    response = reserve(
        show_id=show_id,
        user_id=limit_user,
        seat=fifth_seat,
        idempotency_key=f"metrics-limit-fifth-{uuid.uuid4()}",
    )

    print(
        f"5th reservation response: "
        f"{response.status_code}"
    )

    if response.status_code != 409:
        print(response.text)
        raise RuntimeError(
            "Expected 409 for per-user limit"
        )

    metrics_after_limit = get_metrics()

    user_limit_after = metric_value(
        metrics_after_limit,
        "reservations_declined_total",
        '{reason="user_limit"}',
    )

    user_limit_before_test = metric_value(
        metrics_before_limit,
        "reservations_declined_total",
        '{reason="user_limit"}',
    )

    if user_limit_after != user_limit_before_test + 1:
        raise RuntimeError(
            "user_limit metric did not increase"
        )

    print(
        "PASS: user_limit metric increased"
    )

    # --------------------------------------------------
    # 7. Idempotency conflict
    # --------------------------------------------------

    print("\n[7] Testing idempotency conflict...")

    idempotency_user = f"metrics-idempotency-{uuid.uuid4()}"
    idempotency_key = (
        f"metrics-idempotency-key-{uuid.uuid4()}"
    )

    first_seat = available_seats[7]
    second_seat = available_seats[8]

    response = reserve(
        show_id=show_id,
        user_id=idempotency_user,
        seat=first_seat,
        idempotency_key=idempotency_key,
    )

    print(
        f"First idempotent request: "
        f"{response.status_code}"
    )

    if response.status_code != 201:
        print(response.text)
        raise RuntimeError(
            "Expected first idempotency request "
            "to succeed"
        )

    idempotency_reservation_id = response.json()[
        "reservation_id"
    ]

    created_reservations.append(
        (
            idempotency_reservation_id,
            idempotency_user,
        )
    )

    response = reserve(
        show_id=show_id,
        user_id=idempotency_user,
        seat=second_seat,
        idempotency_key=idempotency_key,
    )

    print(
        f"Different request with same key: "
        f"{response.status_code}"
    )

    if response.status_code != 409:
        print(response.text)
        raise RuntimeError(
            "Expected 409 for idempotency conflict"
        )

    print(
        "PASS: idempotency conflict returned 409"
    )

    # --------------------------------------------------
    # 8. Histogram
    # --------------------------------------------------

    print(
        "\n[8] Testing reservation duration histogram..."
    )

    metrics_after_operations = get_metrics()

    histogram_after = histogram_count(
        metrics_after_operations,
        "reservation_request_duration_seconds",
    )

    if histogram_after <= histogram_before:
        raise RuntimeError(
            "reservation_request_duration_seconds_count "
            "did not increase"
        )

    print(
        "PASS: reservation duration histogram increased"
    )

    # --------------------------------------------------
    # 9. Cleanup
    # --------------------------------------------------

    print("\n[9] Cleaning up test reservations...")

    cleanup_failures = []

    for reservation_id, reservation_user in list(
        created_reservations
    ):
        response = cancel(
            reservation_id=reservation_id,
            user_id=reservation_user,
        )

        if response.status_code != 200:
            cleanup_failures.append(
                (
                    reservation_id,
                    response.status_code,
                    response.text,
                )
            )
        else:
            print(
                f"Cancelled test reservation: "
                f"{reservation_id}"
            )

    if cleanup_failures:
        print(
            "\nWARNING: Some test reservations "
            "could not be cleaned up:"
        )

        for failure in cleanup_failures:
            print(failure)

        raise RuntimeError(
            "Test cleanup failed"
        )

    print(
        "PASS: All test reservations cleaned up"
    )

    # --------------------------------------------------
    # 10. Final reconciliation
    # --------------------------------------------------

    print(
        "\n[10] Checking final metrics reconciliation..."
    )

    final_metrics = get_metrics()

    final_seats_available = metric_value(
        final_metrics,
        "seats_available",
    )

    final_histogram = histogram_count(
        final_metrics,
        "reservation_request_duration_seconds",
    )

    # The Prometheus gauge may have been stale before
    # the test started. The authoritative value is the
    # database-backed show state.
    final_show = get_show(show_id)

    summary = final_show["summary"]

    db_available = summary["available"]
    db_held = summary["held"]
    db_confirmed = summary["confirmed"]
    db_total = summary["total"]

    print(
        f"Initial Prometheus seats_available: "
        f"{seats_available_before}"
    )

    print(
        f"Final Prometheus seats_available:   "
        f"{final_seats_available}"
    )

    print(
        f"Show DB available:                  "
        f"{db_available}"
    )

    print(
        f"Show DB held:                       "
        f"{db_held}"
    )

    print(
        f"Show DB confirmed:                  "
        f"{db_confirmed}"
    )

    print(
        f"Show DB total:                      "
        f"{db_total}"
    )

    # --------------------------------------------------
    # Validate show invariant
    # --------------------------------------------------

    if db_available + db_held + db_confirmed != db_total:
        raise RuntimeError(
            "Show seat invariant failed: "
            "available + held + confirmed != total"
        )

    print(
        "PASS: available + held + confirmed = total"
    )

    # --------------------------------------------------
    # Validate Prometheus gauge
    #
    # reconcile_seats_available() counts all available
    # seats across the database, so it must be a valid
    # non-negative value. The important reconciliation
    # check is that it matches the DB-wide count.
    # --------------------------------------------------

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        settings.database_url,
    )

    async def get_database_available_count() -> int:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM seats
                    WHERE status = 'available'
                    """
                )
            )

            return int(result.scalar_one())


    import asyncio

    database_available_count = asyncio.run(
        get_database_available_count()
    )

    asyncio.run(engine.dispose())

    print(
        f"Database-wide available seats:     "
        f"{database_available_count}"
    )

    if int(final_seats_available) != database_available_count:
        raise RuntimeError(
            "seats_available Prometheus gauge does not "
            "match the database"
        )

    print(
        "PASS: seats_available matches database"
    )

    if final_histogram <= histogram_before:
        raise RuntimeError(
            "Reservation histogram is not increasing"
        )

    print(
        "PASS: reservation duration histogram increased"
    )
    # --------------------------------------------------
    # Final output
    # --------------------------------------------------

    print("\n[11] Final metrics")

    print()
    print_metric(
        final_metrics,
        "reservations_confirmed_total",
    )

    print_metric(
        final_metrics,
        "reservations_cancelled_total",
    )

    print_metric(
        final_metrics,
        "reservations_declined_total",
        '{reason="seat_unavailable"}',
    )

    print_metric(
        final_metrics,
        "reservations_declined_total",
        '{reason="invalid_seat"}',
    )

    print_metric(
        final_metrics,
        "reservations_declined_total",
        '{reason="user_limit"}',
    )

    print_metric(
        final_metrics,
        "rate_limit_rejections_total",
    )

    print(
        "reservation_request_duration_seconds_count"
        f" = {final_histogram}"
    )

    print_metric(
        final_metrics,
        "seats_available",
    )

    print()

    print("=" * 70)
    print("METRICS TEST PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()