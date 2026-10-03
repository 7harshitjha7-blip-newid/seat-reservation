import asyncio
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import jwt

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8000"

SHOW_ID = "dde65d31-afaf-4aca-b5c4-c745c3f611cb"
USER_ID = "rate-limit-test-user"

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


def parse_metric(metrics_text: str, metric_name: str) -> float:
    """
    Extract a Prometheus counter value.

    Example:
        rate_limit_rejections_total 3.0
    """
    for line in metrics_text.splitlines():
        if line.startswith(metric_name + " "):
            return float(line.split()[-1])

    return 0.0


async def main():
    try:
        UUID(SHOW_ID)
    except ValueError:
        print("ERROR: SHOW_ID is not a valid UUID.")
        sys.exit(1)

    token = generate_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    print("\nRATE LIMIT TEST")
    print("=" * 50)
    print(f"User       : {USER_ID}")
    print(f"Show       : {SHOW_ID}")
    print(f"Limit      : {RATE_LIMIT} requests / 10 seconds")
    print("=" * 50)

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=10.0,
    ) as client:

        # --------------------------------------------------
        # 1. Read initial Prometheus metric
        # --------------------------------------------------

        metrics_response = await client.get("/metrics")

        if metrics_response.status_code != 200:
            print("FAIL: Could not read /metrics")
            sys.exit(1)

        initial_metric = parse_metric(
            metrics_response.text,
            "rate_limit_rejections_total",
        )

        print(
            f"Initial rate-limit rejections: "
            f"{initial_metric}"
        )

        # --------------------------------------------------
        # 2. Send requests
        # --------------------------------------------------

        results = []

        for request_number in range(1, RATE_LIMIT + 2):
            response = await client.post(
                f"/shows/{SHOW_ID}/reserve",
                headers=headers,
                json={
                    "seats": ["A1"],
                    "idempotency_key": (
                        f"rate-limit-test-{request_number}"
                    ),
                },
            )

            results.append(response.status_code)

            print(
                f"Request {request_number:2d} "
                f"-> HTTP {response.status_code}"
            )

            if request_number == RATE_LIMIT + 1:
                retry_after = response.headers.get(
                    "Retry-After"
                )

                print(
                    f"Retry-After: {retry_after}"
                )

        # --------------------------------------------------
        # 3. Verify HTTP behavior
        # --------------------------------------------------

        first_ten = results[:RATE_LIMIT]
        eleventh = results[RATE_LIMIT]

        if any(status == 429 for status in first_ten):
            print(
                "\nFAIL: Rate limit triggered "
                "before request 11."
            )
            print(f"Statuses: {results}")
            sys.exit(1)

        if eleventh != 429:
            print(
                "\nFAIL: Request 11 was not rate limited."
            )
            print(f"Statuses: {results}")
            sys.exit(1)

        print(
            "\nPASS: First 10 requests were "
            "accepted by rate limiter."
        )

        print(
            "PASS: Request 11 returned HTTP 429."
        )

        # --------------------------------------------------
        # 4. Verify Prometheus metric
        # --------------------------------------------------

        metrics_response = await client.get("/metrics")

        final_metric = parse_metric(
            metrics_response.text,
            "rate_limit_rejections_total",
        )

        print(
            f"\nFinal rate-limit rejections: "
            f"{final_metric}"
        )

        if final_metric <= initial_metric:
            print(
                "FAIL: rate_limit_rejections_total "
                "did not increase."
            )
            sys.exit(1)

        print(
            "PASS: Prometheus rate-limit metric "
            "increased."
        )

    print("\nRATE LIMIT TEST PASSED")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())