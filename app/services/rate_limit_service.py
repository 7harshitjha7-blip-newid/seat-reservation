from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import rate_limit_rejections_total


RATE_LIMIT = 10
WINDOW_SECONDS = 10


def get_window_start() -> datetime:
    """
    Returns the start of the current fixed-rate-limit window.

    Example:
        14:30:03 -> 14:30:00
        14:30:09 -> 14:30:00
        14:30:10 -> 14:30:10
    """
    now = datetime.now(timezone.utc)

    timestamp = int(now.timestamp())
    window_timestamp = timestamp - (timestamp % WINDOW_SECONDS)

    return datetime.fromtimestamp(
        window_timestamp,
        tz=timezone.utc,
    )


async def check_reservation_rate_limit(
    db: AsyncSession,
    user_id: str,
) -> None:
    window_start = get_window_start()

    query = text(
        """
        INSERT INTO request_rate_limits (
            user_id,
            window_start,
            request_count
        )
        VALUES (
            :user_id,
            :window_start,
            1
        )
        ON CONFLICT (user_id, window_start)
        DO UPDATE SET
            request_count = request_rate_limits.request_count + 1
        RETURNING request_count
        """
    )

    try:
        result = await db.execute(
            query,
            {
                "user_id": user_id,
                "window_start": window_start,
            },
        )

        request_count = result.scalar_one()

        # Commit the rate-limit increment independently.
        #
        # This is intentional:
        # even if the subsequent reservation fails with 409,
        # the request should still count toward the rate limit.
        await db.commit()

    except Exception:
        await db.rollback()
        raise

    if request_count > RATE_LIMIT:
        rate_limit_rejections_total.inc()

        window_end = window_start + timedelta(
            seconds=WINDOW_SECONDS
        )

        retry_after = max(
            1,
            int(
                (
                    window_end
                    - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many reservation requests. "
                "Please try again shortly."
            ),
            headers={
                "Retry-After": str(retry_after),
            },
        )