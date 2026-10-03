import json
import logging
import sys
from datetime import datetime, timezone

from app.core.request_context import get_request_id


class JsonFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:

        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(
            record,
            "request_id",
            None,
        ) or get_request_id()

        if request_id:
            log_data["request_id"] = request_id

        fields = [
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "user_id",
            "show_id",
            "reservation_id",
            "seat_count",
            "amount_paise",
            "reason",
        ]

        for field in fields:
            value = getattr(record, field, None)

            if value is not None:
                log_data[field] = value

        return json.dumps(
            log_data,
            separators=(",", ":"),
        )


def configure_logging():

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()

    root_logger.setLevel(logging.INFO)

    root_logger.handlers.clear()
    root_logger.addHandler(handler)


event_logger = logging.getLogger(
    "seat_reservation.events"
)