"""Structured, UTC-based application logging."""

import json
import logging
from datetime import UTC, datetime

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_SENSITIVE_FIELD_NAMES = frozenset(
    {"api_key", "authorization", "database_url", "jwt", "jwt_secret", "password", "token"}
)


class JsonFormatter(logging.Formatter):
    """Render safe structured log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, value in record.__dict__.items():
            if name in _STANDARD_RECORD_FIELDS or name.lower() in _SENSITIVE_FIELD_NAMES:
                continue
            payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the process root logger with the JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
