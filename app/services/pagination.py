"""Opaque keyset cursors: `<epoch µs>:<key>` in url-safe base64 (µs, so no row repeats or skips)."""

import base64
import binascii
from datetime import UTC, datetime, timedelta

from app.exceptions import ApiError, ErrorCode

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO = timedelta(microseconds=1)


def _bad_cursor() -> ApiError:
    return ApiError(422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"})


def encode_cursor(at: datetime, key: str) -> str:
    """Cursor for the row after which the next page starts."""
    micros = (at - _EPOCH) // _MICRO
    return base64.urlsafe_b64encode(f"{micros}:{key}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """(timestamp, key) from a cursor; 422 `invalid` when it was tampered with."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        micros, key = raw.split(":", 1)
        return _EPOCH + int(micros) * _MICRO, key
    except (ValueError, binascii.Error, UnicodeDecodeError, OverflowError) as exc:
        raise _bad_cursor() from exc
