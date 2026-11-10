"""Structured JSON logging. All secret masking lives here."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Any key containing one of these (case-insensitive) is masked, at any depth.
SENSITIVE_KEYS = frozenset(
    {"password", "token", "cookie", "authorization", "secret", "kf_session", "current", "key"}
)
_REDACTED = "[redacted]"
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in SENSITIVE_KEYS)


def redact(value: Any) -> Any:
    """Copy of `value` with every sensitive key's value masked."""
    if isinstance(value, dict):
        return {k: _REDACTED if _is_sensitive(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message and any `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a record as JSON."""
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        data.update(redact(extras))
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Send every logger (ours, uvicorn, sqlalchemy) to stdout as JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # Access logs would print raw paths, which carry invite, reset and share tokens.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    """The logger for a module: `get_logger(__name__)`."""
    return logging.getLogger(name)
