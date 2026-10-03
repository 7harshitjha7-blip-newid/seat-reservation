import sys
import json
import jwt
import requests

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

USER_1 = "test-user-001"
USER_2 = "test-user-002"

SEATS_TO_RESERVE = ["A1", "A2"]

IDEMPOTENCY_KEY = "lifecycle-test-001"
SECOND_RESERVATION_KEY = "lifecycle-test-after-cancel-001"


# ---------------------------------------------------------
# Authentication
# ---------------------------------------------------------

def generate_token(user_id: str, role: str = "user") -> str:
    payload = {
        "sub": user_id,
        "role": role,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def auth_headers(user_id: str):
    token = generate_token(user_id)

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def print_response(response):
    print(f"HTTP {response.status_code}")

    try:
        print(json.dumps(response.json(), indent=2))
    except Exception:
        print(response.text)

    print()


def fail(message: str):
    print()
    print("=" * 70)
    print("TEST FAILED")
    print("=" * 70)
    print(message)
    print("=" * 70)
    sys.exit(1)


def assert_status(response, expected_status: int, step: str):
    if response.status_code != expected_status:
        print_response(response)
        fail(
            f"{step}: expected HTTP {expected_status}, "
            f"got HTTP {response.status_code}"
        )


def get_show(show_id: str, user_id: str = USER_1):
    response = requests.get(
        f"{BASE_URL}/shows/{show_id}",
        headers=auth_headers(user_id),
        timeout=10,
    )

    return response


def get_seat_status(show_data, seat_number: str):
    for seat in show_data["seats"]:
        if seat["seat_number"] == seat_number:
            return seat["status"]

    return None


def assert_invariant(show_data, step: str):
    summary = show_data["summary"]

    total = summary["total"]
    available = summary["available"]
    held = summary["held"]
    confirmed = summary["confirmed"]

    if available + held + confirmed != total:
        fail(
            f"{step}: seat-count invariant failed\n"
            f"total={total}, "
            f"available={available}, "
            f"held={held}, "
            f"confirmed={confirmed}"
        )


# ---------------------------------------------------------
# Main test
# ---------------------------------------------------------

def main():

    if len(sys.argv) != 2:
        print(
            "Usage:\n"
            "  python scripts/test_reservation_lifecycle.py <show_id>\n\n"
            "Example:\n"
            "  python scripts/test_reservation_lifecycle.py "
            "912c2c98-b084-44e9-a744-1449c186d53f"
        )
        sys.exit(1)

    show_id = sys.argv[1]

    print("=" * 70)
    print("RESERVATION LIFECYCLE TEST")
    print("=" * 70)
    print(f"Base URL : {BASE_URL}")
    print(f"Show ID  : {show_id}")
    print(f"User     : {USER_1}")
    print(f"Seats    : {SEATS_TO_RESERVE}")
    print("=" * 70)

    # -----------------------------------------------------
    # 1. Check show before reservation
    # -----------------------------------------------------

    print("\n[1/9] Checking show before reservation...")

    response = get_show(show_id)

    assert_status(
        response,
        200,
        "Initial GET /shows",
    )

    show = response.json()

    assert_invariant(
        show,
        "Initial show",
    )

    print(
        f"Initial state: "
        f"total={show['summary']['total']} "
        f"available={show['summary']['available']} "
        f"held={show['summary']['held']} "
        f"confirmed={show['summary']['confirmed']}"
    )

    for seat in SEATS_TO_RESERVE:

        status = get_seat_status(
            show,
            seat,
        )

        if status != "available":
            fail(
                f"Seat {seat} must be available before "
                f"starting the test. Current status: {status}"
            )

    print("PASS")

    # -----------------------------------------------------
    # 2. Create reservation
    # -----------------------------------------------------

    print("\n[2/9] Creating reservation...")

    reserve_payload = {
        "seats": SEATS_TO_RESERVE,
        "idempotency_key": IDEMPOTENCY_KEY,
    }

    response = requests.post(
        f"{BASE_URL}/shows/{show_id}/reserve",
        headers=auth_headers(USER_1),
        json=reserve_payload,
        timeout=10,
    )

    assert_status(
        response,
        201,
        "Create reservation",
    )

    reservation = response.json()

    reservation_id = reservation["reservation_id"]

    print(f"Reservation ID: {reservation_id}")
    print(f"Status: {reservation['status']}")
    print(f"Amount: {reservation['amount_paise']} paise")

    if reservation["status"] != "confirmed":
        fail(
            "Reservation should have status 'confirmed'"
        )

    if reservation["user_id"] != USER_1:
        fail(
            "Reservation user_id does not match authenticated user"
        )

    print("PASS")

    # -----------------------------------------------------
    # 3. Verify seats are confirmed
    # -----------------------------------------------------

    print("\n[3/9] Verifying seats became confirmed...")

    response = get_show(show_id)

    assert_status(
        response,
        200,
        "GET show after reservation",
    )

    show = response.json()

    assert_invariant(
        show,
        "After reservation",
    )

    for seat in SEATS_TO_RESERVE:

        status = get_seat_status(
            show,
            seat,
        )

        if status != "confirmed":
            fail(
                f"Seat {seat} should be confirmed, "
                f"got {status}"
            )

    print("PASS")

    # -----------------------------------------------------
    # 4. Cancel reservation
    # -----------------------------------------------------

    print("\n[4/9] Cancelling reservation...")

    response = requests.post(
        f"{BASE_URL}/reservations/{reservation_id}/cancel",
        headers=auth_headers(USER_1),
        timeout=10,
    )

    assert_status(
        response,
        200,
        "Cancel reservation",
    )

    cancelled = response.json()

    print(f"Status after cancellation: {cancelled['status']}")

    if cancelled["status"] != "cancelled":
        fail(
            "Reservation should have status 'cancelled'"
        )

    print("PASS")

    # -----------------------------------------------------
    # 5. Verify seats released
    # -----------------------------------------------------

    print("\n[5/9] Verifying seats were released...")

    response = get_show(show_id)

    assert_status(
        response,
        200,
        "GET show after cancellation",
    )

    show = response.json()

    assert_invariant(
        show,
        "After cancellation",
    )

    for seat in SEATS_TO_RESERVE:

        status = get_seat_status(
            show,
            seat,
        )

        if status != "available":
            fail(
                f"Seat {seat} should be available after "
                f"cancellation, got {status}"
            )

    print(
        f"Final state after cancellation: "
        f"total={show['summary']['total']} "
        f"available={show['summary']['available']} "
        f"held={show['summary']['held']} "
        f"confirmed={show['summary']['confirmed']}"
    )

    print("PASS")

    # -----------------------------------------------------
    # 6. Cancel again
    # -----------------------------------------------------

    print("\n[6/9] Testing repeated cancellation...")

    response = requests.post(
        f"{BASE_URL}/reservations/{reservation_id}/cancel",
        headers=auth_headers(USER_1),
        timeout=10,
    )

    assert_status(
        response,
        200,
        "Repeated cancellation",
    )

    repeated_cancel = response.json()

    if repeated_cancel["status"] != "cancelled":
        fail(
            "Repeated cancellation changed the reservation "
            "to an unexpected state"
        )

    print("PASS")

    # -----------------------------------------------------
    # 7. Ownership test
    # -----------------------------------------------------

    print("\n[7/9] Testing cancellation by another user...")

    response = requests.post(
        f"{BASE_URL}/reservations/{reservation_id}/cancel",
        headers=auth_headers(USER_2),
        timeout=10,
    )

    assert_status(
        response,
        403,
        "Unauthorized cancellation",
    )

    print("PASS")

    # -----------------------------------------------------
    # 8. Reserve released seats again
    # -----------------------------------------------------

    print("\n[8/9] Reserving released seats again...")

    second_payload = {
        "seats": SEATS_TO_RESERVE,
        "idempotency_key": SECOND_RESERVATION_KEY,
    }

    response = requests.post(
        f"{BASE_URL}/shows/{show_id}/reserve",
        headers=auth_headers(USER_2),
        json=second_payload,
        timeout=10,
    )

    assert_status(
        response,
        201,
        "Reserve released seats",
    )

    second_reservation = response.json()

    print(
        f"New reservation ID: "
        f"{second_reservation['reservation_id']}"
    )

    if second_reservation["status"] != "confirmed":
        fail(
            "Released seats should be reservable again"
        )

    print("PASS")

    # -----------------------------------------------------
    # 9. Final verification
    # -----------------------------------------------------

    print("\n[9/9] Final consistency check...")

    response = get_show(
        show_id,
        user_id=USER_2,
    )

    assert_status(
        response,
        200,
        "Final GET /shows",
    )

    final_show = response.json()

    assert_invariant(
        final_show,
        "Final show",
    )

    for seat in SEATS_TO_RESERVE:

        status = get_seat_status(
            final_show,
            seat,
        )

        if status != "confirmed":
            fail(
                f"Seat {seat} should be confirmed after "
                f"second reservation, got {status}"
            )

    print(
        f"Final state: "
        f"total={final_show['summary']['total']} "
        f"available={final_show['summary']['available']} "
        f"held={final_show['summary']['held']} "
        f"confirmed={final_show['summary']['confirmed']}"
    )

    print("PASS")

    # -----------------------------------------------------
    # Final result
    # -----------------------------------------------------

    print()
    print("=" * 70)
    print("RESERVATION LIFECYCLE TEST PASSED")
    print("=" * 70)

    print()
    print("Verified:")
    print("  ✓ Reservation creation")
    print("  ✓ Reservation confirmation")
    print("  ✓ Seat confirmation")
    print("  ✓ Reservation cancellation")
    print("  ✓ Seat release")
    print("  ✓ Repeated cancellation is safe")
    print("  ✓ Cross-user cancellation blocked")
    print("  ✓ Released seats can be reserved again")
    print("  ✓ Seat-count invariant maintained")
    print("=" * 70)


if __name__ == "__main__":
    main()