import asyncio
import sys
import json
from collections import Counter

import httpx
import jwt

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"

OWNER_USER = "cancel-concurrency-owner"
BUYER_USER = "cancel-concurrency-buyer"

SEAT_TO_TEST = "A1"

# INITIAL_IDEMPOTENCY_KEY = "cancel-concurrency-initial-001"

REQUEST_TIMEOUT = 15.0


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


def print_response(label: str, response: httpx.Response):
    print(f"\n--- {label} ---")
    print(f"HTTP {response.status_code}")

    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)


async def get_show(
    client: httpx.AsyncClient,
    show_id: str,
    user_id: str,
):
    return await client.get(
        f"{BASE_URL}/shows/{show_id}",
        headers=headers(user_id),
        timeout=REQUEST_TIMEOUT,
    )


def get_seat_status(show_data: dict, seat_number: str):
    for seat in show_data["seats"]:
        if seat["seat_number"] == seat_number:
            return seat["status"]

    return None


def get_seat_reservation_ownership(
    show_data: dict,
    seat_number: str,
):
    """
    GET /shows currently exposes seat status but not reservation_id.

    This helper intentionally only checks the public seat status.
    """
    return get_seat_status(show_data, seat_number)


def assert_invariant(show_data: dict):
    summary = show_data["summary"]

    total = summary["total"]
    available = summary["available"]
    held = summary["held"]
    confirmed = summary["confirmed"]

    if available + held + confirmed != total:
        raise AssertionError(
            "Seat invariant failed: "
            f"total={total}, "
            f"available={available}, "
            f"held={held}, "
            f"confirmed={confirmed}"
        )


async def reserve_initial_seat(
    client: httpx.AsyncClient,
    show_id: str,
    iteration: int,
):
    print("\n[1/6] Creating initial reservation...")

    idempotency_key = (
        f"cancel-concurrency-initial-{iteration:04d}"
    )

    response = await client.post(
        f"{BASE_URL}/shows/{show_id}/reserve",
        headers=headers(OWNER_USER),
        json={
            "seats": [SEAT_TO_TEST],
            "idempotency_key": idempotency_key,
        },
        timeout=REQUEST_TIMEOUT,
    )

    print_response("Initial reservation", response)

    if response.status_code != 201:
        raise AssertionError(
            "Initial reservation failed. "
            f"Expected 201, got {response.status_code}"
        )

    data = response.json()

    if data["status"] != "confirmed":
        raise AssertionError(
            f"Expected confirmed reservation, "
            f"got {data['status']}"
        )

    if data["user_id"] != OWNER_USER:
        raise AssertionError(
            "Initial reservation user_id does not match "
            "the authenticated owner"
        )

    if not data["seats"]:
        raise AssertionError(
            "Initial reservation returned zero seats"
        )

    if SEAT_TO_TEST not in [
        seat["seat_number"]
        for seat in data["seats"]
    ]:
        raise AssertionError(
            f"Initial reservation does not contain "
            f"{SEAT_TO_TEST}"
        )

    return data["reservation_id"]

async def run_concurrent_operations(
    client: httpx.AsyncClient,
    show_id: str,
    reservation_id: str,
    iteration: int,
):
    print("\n[2/6] Launching cancellation and reservation concurrently...")

    cancel_task = asyncio.create_task(
        client.post(
            f"{BASE_URL}/reservations/{reservation_id}/cancel",
            headers=headers(OWNER_USER),
            timeout=REQUEST_TIMEOUT,
        )
    )

    reserve_task = asyncio.create_task(
        client.post(
            f"{BASE_URL}/shows/{show_id}/reserve",
            headers=headers(BUYER_USER),
            json={
                "seats": [SEAT_TO_TEST],
                "idempotency_key": (
                    f"cancel-concurrency-buyer-{iteration:04d}"
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )
    )

    results = await asyncio.gather(
        cancel_task,
        reserve_task,
        return_exceptions=True,
    )

    cancel_result = results[0]
    reserve_result = results[1]

    if isinstance(cancel_result, Exception):
        print(
            "\nCANCEL REQUEST EXCEPTION:",
            repr(cancel_result),
        )
        raise AssertionError(
            "Cancellation request raised a client-side exception"
        )

    if isinstance(reserve_result, Exception):
        print(
            "\nRESERVATION REQUEST EXCEPTION:",
            repr(reserve_result),
        )
        raise AssertionError(
            "Reservation request raised a client-side exception"
        )

    print_response(
        "Cancellation result",
        cancel_result,
    )

    print_response(
        "Concurrent reservation result",
        reserve_result,
    )

    return cancel_result, reserve_result


async def verify_final_state(
    client: httpx.AsyncClient,
    show_id: str,
    cancel_response: httpx.Response,
    reserve_response: httpx.Response,
):
    print("\n[3/6] Checking final show state...")

    response = await get_show(
        client,
        show_id,
        BUYER_USER,
    )

    print_response(
        "Final show state",
        response,
    )

    if response.status_code != 200:
        raise AssertionError(
            f"Final GET /shows failed with {response.status_code}"
        )

    show = response.json()

    assert_invariant(show)

    seat_status = get_seat_status(
        show,
        SEAT_TO_TEST,
    )

    print(
        f"\nFinal {SEAT_TO_TEST} status: "
        f"{seat_status}"
    )

    return show, seat_status


def validate_outcome(
    cancel_response: httpx.Response,
    reserve_response: httpx.Response,
    final_status: str,
):
    print("\n[4/6] Validating concurrency outcome...")

    cancel_status = cancel_response.status_code
    reserve_status = reserve_response.status_code

    print(
        f"Cancellation HTTP status : {cancel_status}"
    )
    print(
        f"Reservation HTTP status  : {reserve_status}"
    )
    print(
        f"Final seat status         : {final_status}"
    )

    # -------------------------------------------------------------
    # Valid outcome 1:
    #
    # Cancellation wins first.
    #
    # Cancellation:
    #   200
    #
    # Reservation:
    #   201
    #
    # Final:
    #   confirmed
    #
    # -------------------------------------------------------------
    if (
        cancel_status == 200
        and reserve_status == 201
        and final_status == "confirmed"
    ):
        print(
            "\nVALID OUTCOME:"
            "\nCancellation committed before reservation."
            "\nBuyer successfully reserved the released seat."
        )
        return

    # -------------------------------------------------------------
    # Valid outcome 2:
    #
    # Reservation reaches the seat first.
    #
    # Reservation:
    #   409
    #
    # Cancellation:
    #   200
    #
    # Final:
    #   available
    #
    # -------------------------------------------------------------
    if (
        cancel_status == 200
        and reserve_status == 409
        and final_status == "available"
    ):
        print(
            "\nVALID OUTCOME:"
            "\nReservation lost the race."
            "\nOriginal owner successfully cancelled."
        )
        return

    raise AssertionError(
        "\nINVALID CONCURRENCY OUTCOME\n"
        f"Cancellation status = {cancel_status}\n"
        f"Reservation status  = {reserve_status}\n"
        f"Final seat status    = {final_status}\n"
        "\nExpected one of:\n"
        "  cancel=200, reserve=201, final=confirmed\n"
        "  cancel=200, reserve=409, final=available"
    )


async def test_repeated_run(
    client: httpx.AsyncClient,
    show_id: str,
    iterations: int,
):
    print("\n[5/6] Repeating concurrency test...")

    outcomes = Counter()

    for iteration in range(1, iterations + 1):
        print(
            f"\nIteration {iteration}/{iterations}"
        )

        reservation_id = await reserve_initial_seat(
            client,
            show_id,
            iteration,
        )

        cancel_response, reserve_response = (
            await run_concurrent_operations(
                client,
                show_id,
                reservation_id,
                iteration,
            )
        )

        show, final_status = await verify_final_state(
            client,
            show_id,
            cancel_response,
            reserve_response,
        )

        validate_outcome(
            cancel_response,
            reserve_response,
            final_status,
        )

        outcome = (
            cancel_response.status_code,
            reserve_response.status_code,
            final_status,
        )

        outcomes[outcome] += 1

        # ---------------------------------------------------------
        # If buyer won, clean up the buyer reservation so that the
        # next iteration starts from a known state.
        # ---------------------------------------------------------
        if reserve_response.status_code == 201:
            buyer_reservation = reserve_response.json()

            buyer_reservation_id = (
                buyer_reservation["reservation_id"]
            )

            cleanup_response = await client.post(
                f"{BASE_URL}/reservations/"
                f"{buyer_reservation_id}/cancel",
                headers=headers(BUYER_USER),
                timeout=REQUEST_TIMEOUT,
            )

            if cleanup_response.status_code != 200:
                print_response(
                    "Buyer cleanup failed",
                    cleanup_response,
                )

                raise AssertionError(
                    "Could not clean up buyer reservation "
                    "after successful concurrent reservation"
                )

    print("\nOutcome distribution:")

    for outcome, count in outcomes.items():
        cancel_status, reserve_status, final_status = outcome

        print(
            f"  cancel={cancel_status}, "
            f"reserve={reserve_status}, "
            f"final={final_status}"
            f" -> {count}"
        )


async def final_database_state(
    client: httpx.AsyncClient,
    show_id: str,
):
    print("\n[6/6] Final consistency check...")

    response = await get_show(
        client,
        show_id,
        BUYER_USER,
    )

    if response.status_code != 200:
        raise AssertionError(
            f"Final consistency GET failed: "
            f"{response.status_code}"
        )

    show = response.json()

    assert_invariant(show)

    summary = show["summary"]

    print(
        f"total={summary['total']} "
        f"available={summary['available']} "
        f"held={summary['held']} "
        f"confirmed={summary['confirmed']}"
    )

    print(
        "\nSeat-count invariant:"
        f" {summary['available']} + "
        f"{summary['held']} + "
        f"{summary['confirmed']} = "
        f"{summary['total']}"
    )

    print("\nPASS")


async def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  python -m scripts.test_cancel_reserve_concurrency "
            "<show_id> [iterations]\n\n"
            "Example:\n"
            "  python -m scripts.test_cancel_reserve_concurrency "
            "ef320cd9-477e-4ee6-9e18-e24327ed4f95 10"
        )
        sys.exit(1)

    show_id = sys.argv[1]

    iterations = (
        int(sys.argv[2])
        if len(sys.argv) >= 3
        else 10
    )

    if iterations < 1:
        print("Iterations must be >= 1")
        sys.exit(1)

    print("=" * 70)
    print("CANCELLATION vs RESERVATION CONCURRENCY TEST")
    print("=" * 70)

    print(f"Base URL       : {BASE_URL}")
    print(f"Show ID        : {show_id}")
    print(f"Seat           : {SEAT_TO_TEST}")
    print(f"Owner user     : {OWNER_USER}")
    print(f"Buyer user     : {BUYER_USER}")
    print(f"Iterations     : {iterations}")

    print("=" * 70)

    async with httpx.AsyncClient() as client:

        # ---------------------------------------------------------
        # Make sure the test seat starts available.
        # ---------------------------------------------------------
        print("\nChecking initial state...")

        response = await get_show(
            client,
            show_id,
            OWNER_USER,
        )

        if response.status_code != 200:
            print_response(
                "Initial show lookup",
                response,
            )

            raise AssertionError(
                "Could not load show"
            )

        show = response.json()

        assert_invariant(show)

        initial_status = get_seat_status(
            show,
            SEAT_TO_TEST,
        )

        print(
            f"Initial {SEAT_TO_TEST} status: "
            f"{initial_status}"
        )

        if initial_status != "available":
            raise AssertionError(
                f"{SEAT_TO_TEST} must be available "
                f"before starting this test. "
                f"Current status: {initial_status}"
            )

        await test_repeated_run(
            client,
            show_id,
            iterations,
        )

        await final_database_state(
            client,
            show_id,
        )

    print()
    print("=" * 70)
    print("CANCELLATION CONCURRENCY TEST PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())