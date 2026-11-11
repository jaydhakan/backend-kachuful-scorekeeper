"""Fixed-window rate limits in Postgres (plan §10). No Redis."""

import math
import time
from collections import deque
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
# Ishaara: actions per game, creates per user.
ISHAARA_ACTION = Limit("ishaara-action", 60, 60)
ISHAARA_CREATE = Limit("ishaara-create", 20, 3600)


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


class Burst:
    """In-memory sliding-window limits, per worker: for taps too hot for the `rate_limits` table.

    Each worker counts on its own, so with N workers a client gets at most N times the limit.
    """

    def __init__(self, *rules: tuple[int, float]) -> None:
        self.rules = rules
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> bool:
        """Count a tap; False when any rule (max hits per window seconds) is already full."""
        now = time.monotonic()
        longest = max(window for _, window in self.rules)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > longest:
            hits.popleft()
        for max_hits, window in self.rules:
            if sum(1 for at in hits if now - at <= window) >= max_hits:
                return False
        hits.append(now)
        if len(self._hits) > 10_000:
            # Forget idle keys so memory stays bounded.
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > longest]:
                del self._hits[k]
        return True


# Spectator reactions: a viewer at most 1 a second and 20 a minute; a link at most 15 a second.
REACTION_VIEWER = Burst((1, 1.0), (20, 60.0))
REACTION_LINK = Burst((15, 1.0))
