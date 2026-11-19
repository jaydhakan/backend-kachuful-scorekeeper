"""An online Spades follower's SSE stream: `state` frames, `seat-presence`, the kind's events.

The hub follows each account (not a seat), so a seat move never hands anyone another seat's
frame. Presence arrives as the accounts following the table; this stream maps them onto seats
with the table it last sent, and sends `{seats: boolean[4]}` whenever that changes.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.live.hub import SEAT_PRESENCE, LiveMessage, hub
from app.live.sse import PING_EVERY_S, sse
from app.logger import get_logger

logger = get_logger(__name__)
# The ping goes out after this long even when the rev lookup has not answered.
PING_LOOKUP_TIMEOUT_S = 5.0


class _Seats:
    """The followed accounts and the table's seats, and the last presence frame sent."""

    def __init__(self) -> None:
        self.users: set[str] | None = None
        self.seats: list[str | None] = [None, None, None, None]
        self.sent: list[bool] | None = None

    def table(self, data: str) -> None:
        table: dict[str, Any] = json.loads(data).get("table", {})
        self.seats = [s["who"]["userId"] if s.get("who") else None for s in table.get("seats", [])]

    def present(self, data: str) -> None:
        self.users = set(json.loads(data).get("users", []))

    def frame(self) -> str | None:
        """A presence frame when it differs from the last one sent."""
        if self.users is None:
            return None
        users = self.users
        seats = [u is not None and u in users for u in self.seats]
        if seats == self.sent:
            return None
        self.sent = seats
        return json.dumps({"seats": seats})


def _render(message: LiveMessage, seats: _Seats) -> list[str]:
    """The SSE frames for one hub message (presence only when it changed)."""
    if message.data is None:
        return []
    if message.kind == SEAT_PRESENCE:
        seats.present(message.data)
    elif message.kind == "state":
        seats.table(message.data)
        out = [sse("state", message.data, message.rev)]
        frame = seats.frame()
        return out + ([sse(SEAT_PRESENCE, frame)] if frame else [])
    else:
        return [sse(message.kind, message.data)]
    frame = seats.frame()
    return [sse(SEAT_PRESENCE, frame)] if frame else []


async def _ping_data(on_ping: Callable[[], Awaitable[int | None]]) -> str:
    """`{"rev": n}` (the table's current rev), or `{}` if the lookup fails or is slow.

    A slow or failing database must not hold the ping back or end the stream.
    """
    try:
        rev = await asyncio.wait_for(on_ping(), PING_LOOKUP_TIMEOUT_S)
    except Exception:
        logger.exception("table ping without a rev")
        return "{}"
    return "{}" if rev is None else json.dumps({"rev": rev})


async def table_stream(
    game_id: uuid.UUID,
    queue: asyncio.Queue[LiveMessage],
    first: LiveMessage,
    *,
    send_first: bool,
    on_ping: Callable[[], Awaitable[int | None]],
    ping_every_s: float = PING_EVERY_S,
) -> AsyncIterator[str]:
    """Frames until the table is deleted or the follower leaves; `on_ping` runs before each ping."""
    seats = _Seats()
    if first.data is not None:
        seats.table(first.data)
        if send_first:
            yield sse("state", first.data, first.rev)
    try:
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), ping_every_s)
            except TimeoutError:
                yield sse("ping", await _ping_data(on_ping))
                continue
            if message.kind == "closed":
                return
            for frame in _render(message, seats):
                yield frame
    finally:
        hub.unsubscribe_game(game_id, queue)
