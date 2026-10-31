"""Fixed-window rate limits in Postgres (plan §10). No Redis."""

import math
from dataclasses import dataclass

from app.db.session import get_session_factory
from app.domain.time import from_ms, now_utc
from app.exceptions import ApiError, ErrorCode
from app.repositories.rate_limits import rate_limits_repository


@dataclass(frozen=True)
class Limit:
    """At most `max_hits` per `window_s` seconds for one key."""

    name: str
    max_hits: int
    window_s: int


LOGIN = Limit("login", 10, 600)
TOKEN = Limit("token", 30, 600)
UPLOAD = Limit("upload", 120, 60)
PLAYER_CHECK = Limit("check", 60, 60)


async def enforce(limit: Limit, key: str) -> None:
    """Count this request; 429 `rate_limited` with `retryAfter` (seconds) once over the limit.

    Counted in its own committed transaction, so failed requests still count.
    """
    now = now_utc()
    window_ms = limit.window_s * 1000
    start_ms = int(now.timestamp() * 1000) // window_ms * window_ms
    async with get_session_factory()() as session:
        count = await rate_limits_repository.hit(session, f"{limit.name}:{key}", from_ms(start_ms))
        await session.commit()
    if count > limit.max_hits:
        retry_after = math.ceil((start_ms + window_ms) / 1000 - now.timestamp())
        raise ApiError(
            429,
            ErrorCode.RATE_LIMITED,
            "Too many attempts. Try again shortly.",
            retryAfter=max(retry_after, 1),
        )
