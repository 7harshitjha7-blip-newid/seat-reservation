import asyncio
from datetime import datetime, timedelta, timezone
import sys
from uuid import UUID

import httpx
import jwt

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"

SHOW_ID = "dde65d31-afaf-4aca-b5c4-c745c3f611cb"

USER_ID = "concurrent-rate-limit-test-user"

TOTAL_REQUESTS = 50
RATE_LIMIT = 10


def generate_token() -> str:
    payload = {
        "sub": USER_ID,
        "role": "user",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


async def send_request(
    client: httpx.AsyncClient,
    token: str,
    request_number: int,
):
    response = await client.post(
        f"/shows/{SHOW_ID}/reserve",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "seats": [f"TEST-{request_number}"],
            "idempotency_key": (
                f"concurrent-rate-limit-{request_number}"
            ),
        },
    )

    return response.status_code


async def main():
    try:
        UUID(SHOW_ID)
    except ValueError:
        print("ERROR: SHOW_ID is not a valid UUID.")
        sys.exit(1)

    token = generate_token()

    print("\nCONCURRENT RATE LIMIT TEST")
    print("=" * 60)
    print(f"User           : {USER_ID}")
    print(f"Total requests : {TOTAL_REQUESTS}")
    print(f"Rate limit     : {RATE_LIMIT} / 10 seconds")
    print("=" * 60)

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=30.0,
    ) as client:

        tasks = [
            send_request(
                client,
                token,
                request_number,
            )
            for request_number in range(1, TOTAL_REQUESTS + 1)
        ]

        results = await asyncio.gather(*tasks)

    status_counts = {}

    for status_code in results:
        status_counts[status_code] = (
            status_counts.get(status_code, 0) + 1
        )

    print("\nOUTCOME DISTRIBUTION")
    print("=" * 60)

    for status_code in sorted(status_counts):
        print(
            f"HTTP {status_code}: "
            f"{status_counts[status_code]}"
        )

    successful_rate_limit_requests = sum(
        status != 429
        for status in results
    )

    rate_limited_requests = results.count(429)

    server_errors = [
        status
        for status in results
        if status >= 500
    ]

    print("\nVALIDATION")
    print("=" * 60)

    print(
        f"Requests reaching reservation layer : "
        f"{successful_rate_limit_requests}"
    )

    print(
        f"Requests rejected by rate limiter   : "
        f"{rate_limited_requests}"
    )

    print(
        f"5xx responses                       : "
        f"{len(server_errors)}"
    )

    # --------------------------------------------------
    # Validation
    # --------------------------------------------------

    if len(server_errors) > 0:
        print("\nFAIL: Received 5xx responses.")
        print(results)
        sys.exit(1)

    if rate_limited_requests != (
        TOTAL_REQUESTS - RATE_LIMIT
    ):
        print(
            "\nFAIL: Unexpected number of "
            "rate-limited requests."
        )
        print(
            f"Expected 429: "
            f"{TOTAL_REQUESTS - RATE_LIMIT}"
        )
        print(
            f"Actual 429: "
            f"{rate_limited_requests}"
        )
        print(results)
        sys.exit(1)

    if successful_rate_limit_requests != RATE_LIMIT:
        print(
            "\nFAIL: Unexpected number of requests "
            "passing the rate limiter."
        )
        print(
            f"Expected: {RATE_LIMIT}"
        )
        print(
            f"Actual: {successful_rate_limit_requests}"
        )
        print(results)
        sys.exit(1)

    print(
        "\nPASS: Exactly 10 requests passed "
        "the rate limiter."
    )

    print(
        "PASS: Remaining requests returned 429."
    )

    print(
        "PASS: Zero 5xx responses."
    )

    print("\nCONCURRENT RATE LIMIT TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())