import asyncio
import sys
from collections import Counter

import httpx
import jwt

if __name__ == "__main__" and sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"
CONCURRENCY = 500
HOT_SEAT = "A1"


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


async def reserve_seat(
    client: httpx.AsyncClient,
    show_id: str,
    user_id: str,
    index: int,
):
    token = generate_token(user_id)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "seats": [HOT_SEAT],
        "idempotency_key": f"burst-{user_id}-{index}",
    }

    try:
        response = await client.post(
            f"{BASE_URL}/shows/{show_id}/reserve",
            headers=headers,
            json=payload,
        )

        return {
            "user_id": user_id,
            "status_code": response.status_code,
            "body": response.json(),
        }

    except Exception as exc:
        return {
            "user_id": user_id,
            "status_code": 0,
            "body": {
                "error": str(exc),
            },
        }


async def get_show(
    client: httpx.AsyncClient,
    show_id: str,
):
    token = generate_token(
        user_id="burst-monitor",
    )

    headers = {
        "Authorization": f"Bearer {token}",
    }

    response = await client.get(
        f"{BASE_URL}/shows/{show_id}",
        headers=headers,
    )

    response.raise_for_status()

    return response.json()

async def main():
    if len(sys.argv) != 2:
        print(
            "Usage:\n"
            "  python scripts/burst_test.py <show_id>"
        )
        sys.exit(1)

    show_id = sys.argv[1]

    print("=" * 70)
    print("SEAT RESERVATION CONCURRENCY TEST")
    print("=" * 70)
    print(f"Base URL      : {BASE_URL}")
    print(f"Show ID       : {show_id}")
    print(f"Hot seat      : {HOT_SEAT}")
    print(f"Concurrency   : {CONCURRENCY}")
    print("=" * 70)

    timeout = httpx.Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=30.0,
    )

    limits = httpx.Limits(
        max_connections=CONCURRENCY,
        max_keepalive_connections=CONCURRENCY,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
    ) as client:

        # ---------------------------------------------------------
        # 1. Verify show before burst
        # ---------------------------------------------------------

        print("\n[1/3] Checking show before burst...")

        before = await get_show(client, show_id)

        print(
            f"Before: "
            f"total={before['summary']['total']} "
            f"available={before['summary']['available']} "
            f"held={before['summary']['held']} "
            f"confirmed={before['summary']['confirmed']}"
        )

        hot_seat_before = next(
            (
                seat
                for seat in before["seats"]
                if seat["seat_number"] == HOT_SEAT
            ),
            None,
        )

        if hot_seat_before is None:
            print(f"ERROR: {HOT_SEAT} does not exist in this show.")
            sys.exit(1)

        if hot_seat_before["status"] != "available":
            print(
                f"ERROR: {HOT_SEAT} is already "
                f"{hot_seat_before['status']}."
            )
            print("Use a fresh show for the burst test.")
            sys.exit(1)

        # ---------------------------------------------------------
        # 2. Launch concurrent requests
        # ---------------------------------------------------------

        print(
            f"\n[2/3] Launching {CONCURRENCY} concurrent "
            f"requests for {HOT_SEAT}..."
        )

        tasks = [
            reserve_seat(
                client=client,
                show_id=show_id,
                user_id=f"burst-user-{i:04d}",
                index=i,
            )
            for i in range(CONCURRENCY)
        ]

        results = await asyncio.gather(*tasks)

        status_counts = Counter(
            result["status_code"]
            for result in results
        )

        print("\nBurst results:")
        print("-" * 40)

        for status_code, count in sorted(status_counts.items()):
            label = str(status_code)

            if status_code == 201:
                label = "201 CREATED"
            elif status_code == 409:
                label = "409 CONFLICT"
            elif status_code == 0:
                label = "NETWORK/CLIENT ERROR"
            elif status_code >= 500:
                label = f"{status_code} SERVER ERROR"

            print(f"{label:<25} {count}")

        successful = [
            result
            for result in results
            if result["status_code"] == 201
        ]

        conflicts = [
            result
            for result in results
            if result["status_code"] == 409
        ]

        server_errors = [
            result
            for result in results
            if result["status_code"] >= 500
        ]

        client_errors = [
            result
            for result in results
            if result["status_code"] == 0
        ]

        if client_errors:
            print("\nClient/network errors:")
            for result in client_errors[:20]:
                print(
                    f"  {result['user_id']} -> "
                    f"{result['body'].get('error')}"
                )

            if len(client_errors) > 20:
                print(
                    f"  ... and {len(client_errors) - 20} more"
                )

        print("\nSuccessful reservations:")
        for result in successful:
            print(
                f"  {result['user_id']} -> "
                f"{result['body'].get('reservation_id')}"
            )

        # ---------------------------------------------------------
        # 3. Verify final database state through API
        # ---------------------------------------------------------

        print("\n[3/3] Checking final show state...")

        after = await get_show(client, show_id)

        summary = after["summary"]

        print(
            f"After: "
            f"total={summary['total']} "
            f"available={summary['available']} "
            f"held={summary['held']} "
            f"confirmed={summary['confirmed']}"
        )

        hot_seat_after = next(
            (
                seat
                for seat in after["seats"]
                if seat["seat_number"] == HOT_SEAT
            ),
            None,
        )

        print(
            f"Hot seat {HOT_SEAT} final status: "
            f"{hot_seat_after['status']}"
        )

        invariant_total = (
            summary["available"]
            + summary["held"]
            + summary["confirmed"]
        )

        invariant_ok = invariant_total == summary["total"]

        unique_reservation_ids = {
            result["body"].get("reservation_id")
            for result in successful
        }

        # ---------------------------------------------------------
        # Final assertions
        # ---------------------------------------------------------

        print("\n" + "=" * 70)
        print("CONCURRENCY TEST REPORT")
        print("=" * 70)

        checks = {
            "Exactly 1 successful reservation": len(successful) == 1,
            "Exactly 499 conflicts": len(conflicts) == CONCURRENCY - 1,
            "Zero server errors": len(server_errors) == 0,
            "Zero client/network errors": len(client_errors) == 0,
            "Exactly 1 unique reservation ID": len(unique_reservation_ids) == 1,
            "Hot seat is confirmed": (
                hot_seat_after is not None
                and hot_seat_after["status"] == "confirmed"
            ),
            "Seat-count invariant holds": invariant_ok,
        }

        all_passed = True

        for name, passed in checks.items():
            print(
                f"[{'PASS' if passed else 'FAIL'}] "
                f"{name}"
            )

            if not passed:
                all_passed = False

        print("=" * 70)

        if all_passed:
            print("RESULT: CONCURRENCY TEST PASSED")
            print("=" * 70)
            sys.exit(0)

        print("RESULT: CONCURRENCY TEST FAILED")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())