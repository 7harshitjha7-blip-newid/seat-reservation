import asyncio
import os
import statistics
import sys
import time
import uuid
from collections import Counter

import httpx
import jwt


# ============================================================
# Configuration
# ============================================================

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

JWT_SECRET = os.getenv(
    "JWT_SECRET",
    "change-this-to-a-long-random-secret",
)

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

HOT_SEAT_USERS = int(os.getenv("HOT_SEAT_USERS", "100"))
SHOW_SEAT_COUNT = int(os.getenv("SHOW_SEAT_COUNT", "20"))

REQUEST_TIMEOUT = float(
    os.getenv("REQUEST_TIMEOUT", "60")
)

HTTP_CONCURRENCY = int(
    os.getenv("HTTP_CONCURRENCY", "50")
)

ADMIN_USER = "burst-admin"

# Set to 0 to avoid rate limiting during the concurrency test.
# Your production rate-limit test is already covered separately.
USE_RATE_LIMIT_DELAY = float(
    os.getenv("USE_RATE_LIMIT_DELAY", "0")
)


# ============================================================
# Authentication
# ============================================================

def generate_token(user_id: str, role: str) -> str:
    now = int(time.time())

    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + 3600,
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


# ============================================================
# Helpers
# ============================================================

def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0

    values = sorted(values)

    index = (len(values) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)

    if lower == upper:
        return values[lower]

    fraction = index - lower

    return (
        values[lower]
        + (values[upper] - values[lower]) * fraction
    )


def print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# HTTP helpers
# ============================================================

async def create_show(
    client: httpx.AsyncClient,
    admin_token: str,
) -> dict:

    seats = [
        f"A{i:03d}"
        for i in range(1, SHOW_SEAT_COUNT + 1)
    ]

    response = await client.post(
        "/shows",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
        json={
            "name": "Burst Test Show",
            "seats": seats,
            "price_paise": 10000,
            "per_user_limit": 4,
        },
    )

    if response.status_code != 201:
        raise RuntimeError(
            f"Failed to create show: "
            f"{response.status_code} {response.text}"
        )

    return response.json()


async def get_show(
    client: httpx.AsyncClient,
    show_id: str,
    token: str,
) -> dict:

    response = await client.get(
        f"/shows/{show_id}",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch show: "
            f"{response.status_code} {response.text}"
        )

    return response.json()


# ============================================================
# Single reservation request
# ============================================================

async def reserve_one(
    client: httpx.AsyncClient,
    show_id: str,
    seat: str,
    user_number: int,
    semaphore: asyncio.Semaphore,
) -> dict:

    user_id = f"burst-user-{user_number}"

    token = generate_token(
        user_id=user_id,
        role="user",
    )

    idempotency_key = str(uuid.uuid4())

    start = time.perf_counter()

    try:
        async with semaphore:
            response = await client.post(
                f"/shows/{show_id}/reserve",
                headers={
                    "Authorization": f"Bearer {token}",
                },
                json={
                    "seats": [seat],
                    "idempotency_key": idempotency_key,
                },
            )

        duration_ms = (
            time.perf_counter() - start
        ) * 1000

        return {
            "user_id": user_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "body": (
                response.json()
                if response.content
                else {}
            ),
        }

    except Exception as exc:
        duration_ms = (
            time.perf_counter() - start
        ) * 1000

        return {
            "user_id": user_id,
            "status_code": 599,
            "duration_ms": duration_ms,
            "body": {
                "error_type": type(exc).__name__,
                "error": repr(exc),
            },
        }
# ============================================================
# Hot-seat storm
# ============================================================

async def hot_seat_storm(
    client: httpx.AsyncClient,
    show_id: str,
    seat: str,
) -> tuple[list[dict], float]:

    print_section("HOT-SEAT CONCURRENCY TEST")

    print(f"Target seat       : {seat}")
    print(f"Concurrent users  : {HOT_SEAT_USERS}")
    print(f"HTTP concurrency   : {HTTP_CONCURRENCY}")

    start = time.perf_counter()

    semaphore = asyncio.Semaphore(
        HTTP_CONCURRENCY
    )

    tasks = [
        reserve_one(
            client=client,
            show_id=show_id,
            seat=seat,
            user_number=i,
            semaphore=semaphore,
        )
        for i in range(1, HOT_SEAT_USERS + 1)
    ]

    results = await asyncio.gather(*tasks)

    errors = [
        result
        for result in results
        if result["status_code"] == 599
    ]

    if errors:
        print()
        print("=" * 70)
        print("CLIENT-SIDE ERRORS")
        print("=" * 70)

        for error in errors[:20]:
            print(
                f"{error['user_id']} -> "
                f"{error['body']}"
            )

        if len(errors) > 20:
            print(
                f"... and {len(errors) - 20} more errors"
            )

    total_duration = (
        time.perf_counter() - start
    ) * 1000

    return results, total_duration

# ============================================================
# Multi-seat all-or-nothing test
# ============================================================

async def multi_seat_test(
    client: httpx.AsyncClient,
    show_id: str,
) -> dict:

    print_section("MULTI-SEAT ATOMICITY TEST")

    user_id = "multi-seat-user"

    token = generate_token(
        user_id=user_id,
        role="user",
    )

    idempotency_key = str(uuid.uuid4())

    # A002 is already available unless the hot-seat test
    # happened to use it.
    requested_seats = ["A002", "A003"]

    response = await client.post(
        f"/shows/{show_id}/reserve",
        headers={
            "Authorization": f"Bearer {token}",
        },
        json={
            "seats": requested_seats,
            "idempotency_key": idempotency_key,
        },
    )

    result = {
        "status_code": response.status_code,
        "requested_seats": requested_seats,
        "body": (
            response.json()
            if response.content
            else {}
        ),
    }

    print(f"Requested seats : {requested_seats}")
    print(f"HTTP status     : {response.status_code}")

    if response.status_code == 201:
        reserved = [
            seat["seat_number"]
            for seat in result["body"].get(
                "seats",
                [],
            )
        ]

        print(f"Reserved seats  : {reserved}")

        result["atomicity_pass"] = (
            sorted(reserved)
            == sorted(requested_seats)
        )

    elif response.status_code == 409:
        print(
            "Request rejected because at least one "
            "requested seat was unavailable."
        )

        result["atomicity_pass"] = True

    else:
        result["atomicity_pass"] = False

    return result


# ============================================================
# Metrics
# ============================================================

def summarize_results(results: list[dict]) -> dict:

    status_counts = Counter(
        result["status_code"]
        for result in results
    )

    durations = [
        result["duration_ms"]
        for result in results
    ]

    five_xx = sum(
        count
        for status, count in status_counts.items()
        if 500 <= status < 600
    )

    return {
        "total": len(results),
        "status_counts": status_counts,
        "five_xx": five_xx,
        "min_ms": min(durations) if durations else 0,
        "max_ms": max(durations) if durations else 0,
        "mean_ms": (
            statistics.mean(durations)
            if durations
            else 0
        ),
        "p50_ms": percentile(durations, 0.50),
        "p95_ms": percentile(durations, 0.95),
        "p99_ms": percentile(durations, 0.99),
    }


def print_summary(summary: dict) -> None:

    print_section("HOT-SEAT RESULTS")

    print(
        f"Total requests     : "
        f"{summary['total']}"
    )

    for status_code, count in sorted(
        summary["status_counts"].items()
    ):
        print(
            f"HTTP {status_code:<12}: {count}"
        )

    print(
        f"5xx errors         : "
        f"{summary['five_xx']}"
    )

    print()

    print(
        f"Min latency        : "
        f"{summary['min_ms']:.2f} ms"
    )

    print(
        f"Mean latency       : "
        f"{summary['mean_ms']:.2f} ms"
    )

    print(
        f"P50 latency        : "
        f"{summary['p50_ms']:.2f} ms"
    )

    print(
        f"P95 latency        : "
        f"{summary['p95_ms']:.2f} ms"
    )

    print(
        f"P99 latency        : "
        f"{summary['p99_ms']:.2f} ms"
    )

    print(
        f"Max latency        : "
        f"{summary['max_ms']:.2f} ms"
    )


# ============================================================
# Reconciliation
# ============================================================

def reconcile_show(show: dict) -> dict:

    summary = show["summary"]

    total = summary["total"]
    available = summary["available"]
    held = summary["held"]
    confirmed = summary["confirmed"]

    invariant = (
        available
        + held
        + confirmed
        == total
    )

    return {
        "total": total,
        "available": available,
        "held": held,
        "confirmed": confirmed,
        "invariant": invariant,
    }


def print_reconciliation(result: dict) -> None:

    print_section("SEAT RECONCILIATION")

    print(
        f"Total seats       : "
        f"{result['total']}"
    )

    print(
        f"Available         : "
        f"{result['available']}"
    )

    print(
        f"Held              : "
        f"{result['held']}"
    )

    print(
        f"Confirmed         : "
        f"{result['confirmed']}"
    )

    calculated = (
        result["available"]
        + result["held"]
        + result["confirmed"]
    )

    print(
        f"Available + Held + Confirmed : "
        f"{calculated}"
    )

    print(
        f"Invariant         : "
        f"{'PASS' if result['invariant'] else 'FAIL'}"
    )


# ============================================================
# Main
# ============================================================

async def main() -> int:

    print_section("SEAT RESERVATION BURST TEST")

    print(f"Base URL          : {BASE_URL}")
    print(f"Hot-seat users    : {HOT_SEAT_USERS}")
    print(f"Show seats        : {SHOW_SEAT_COUNT}")

    timeout = httpx.Timeout(
        connect=10.0,
        read=REQUEST_TIMEOUT,
        write=10.0,
        pool=REQUEST_TIMEOUT,
    )

    limits = httpx.Limits(
        max_connections=HTTP_CONCURRENCY,
        max_keepalive_connections=HTTP_CONCURRENCY,
    )

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=timeout,
        limits=limits,
    ) as client:

        # ----------------------------------------------------
        # Create show
        # ----------------------------------------------------

        print_section("SETUP")

        admin_token = generate_token(
            user_id=ADMIN_USER,
            role="admin",
        )

        show = await create_show(
            client=client,
            admin_token=admin_token,
        )

        show_id = show["id"]

        print(f"Show ID           : {show_id}")

        seats = show["seats"]

        available_seats = [
            seat["seat_number"]
            for seat in seats
            if seat["status"] == "available"
        ]

        if not available_seats:
            print("FAIL: no available seats")
            return 1

        hot_seat = available_seats[0]

        print(f"Hot seat          : {hot_seat}")

        # ----------------------------------------------------
        # Hot-seat test
        # ----------------------------------------------------

        results, storm_duration = await hot_seat_storm(
            client=client,
            show_id=show_id,
            seat=hot_seat,
        )

        summary = summarize_results(results)

        print_summary(summary)

        created = summary["status_counts"].get(
            201,
            0,
        )

        conflicts = summary["status_counts"].get(
            409,
            0,
        )

        # ----------------------------------------------------
        # Correctness assertions
        # ----------------------------------------------------

        hot_seat_pass = (
            created == 1
            and conflicts == HOT_SEAT_USERS - 1
            and summary["five_xx"] == 0
        )

        print()
        print(
            "Hot-seat correctness : "
            f"{'PASS' if hot_seat_pass else 'FAIL'}"
        )

        print(
            f"Total storm time     : "
            f"{storm_duration:.2f} ms"
        )

        # ----------------------------------------------------
        # Multi-seat
        # ----------------------------------------------------

        multi_result = await multi_seat_test(
            client=client,
            show_id=show_id,
        )

        print(
            "Multi-seat atomicity : "
            f"{'PASS' if multi_result['atomicity_pass'] else 'FAIL'}"
        )

        # ----------------------------------------------------
        # Final state
        # ----------------------------------------------------

        final_token = generate_token(
            user_id="burst-observer",
            role="user",
        )

        final_show = await get_show(
            client=client,
            show_id=show_id,
            token=final_token,
        )

        reconciliation = reconcile_show(
            final_show
        )

        print_reconciliation(
            reconciliation
        )

        # ----------------------------------------------------
        # Final verdict
        # ----------------------------------------------------

        overall_pass = (
            hot_seat_pass
            and multi_result["atomicity_pass"]
            and reconciliation["invariant"]
        )

        print_section("FINAL RESULT")

        print(
            f"Hot-seat test       : "
            f"{'PASS' if hot_seat_pass else 'FAIL'}"
        )

        print(
            f"Multi-seat test     : "
            f"{'PASS' if multi_result['atomicity_pass'] else 'FAIL'}"
        )

        print(
            f"Reconciliation      : "
            f"{'PASS' if reconciliation['invariant'] else 'FAIL'}"
        )

        print()

        print(
            "BURST TEST           : "
            f"{'PASSED' if overall_pass else 'FAILED'}"
        )

        return 0 if overall_pass else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        exit_code = 130
    except Exception as exc:
        print()
        print("=" * 70)
        print("BURST TEST FAILED WITH EXCEPTION")
        print("=" * 70)
        print(str(exc))
        exit_code = 1

    sys.exit(exit_code)