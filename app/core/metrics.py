from prometheus_client import Counter, Gauge, Histogram


reservations_confirmed_total = Counter(
    "reservations_confirmed_total",
    "Total number of successfully confirmed reservations",
)


reservations_declined_total = Counter(
    "reservations_declined_total",
    "Total number of declined reservation attempts",
    ["reason"],
)


reservations_cancelled_total = Counter(
    "reservations_cancelled_total",
    "Total number of successfully cancelled reservations",
)


seats_available = Gauge(
    "seats_available",
    "Number of currently available seats",
)


reservation_request_duration_seconds = Histogram(
    "reservation_request_duration_seconds",
    "Reservation request duration in seconds",
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Total number of requests rejected by rate limiting",
)