from fastapi import FastAPI
from fastapi.responses import Response
from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.api.shows import router as shows_router
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.api.reservations import (
    router as reservations_router,
)
# from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services.metrics_service import reconcile_seats_available
import logging
import time
import uuid

from app.core.request_context import (
    set_request_id,
    reset_request_id,
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import configure_logging

configure_logging()

logger = logging.getLogger("seat_reservation")


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        incoming_request_id = request.headers.get(
            "X-Request-ID"
        )

        if incoming_request_id:
            try:
                request_id = str(
                    uuid.UUID(incoming_request_id)
                )
            except ValueError:
                request_id = str(uuid.uuid4())
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id

        context_token = set_request_id(request_id)

        start_time = time.perf_counter()

        status_code = 500

        try:
            response = await call_next(request)

            status_code = response.status_code

            response.headers["X-Request-ID"] = request_id

            return response

        finally:
            duration_ms = round(
                (time.perf_counter() - start_time) * 1000,
                2,
            )

            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )

            reset_request_id(context_token)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):

    async def dispatch(
        self,
        request: Request,
        call_next,
    ):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as db:
        await reconcile_seats_available(db)

    yield


app = FastAPI(
    title="Seat Reservation System",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(health_router)
app.include_router(shows_router)
app.include_router(reservations_router)

@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )