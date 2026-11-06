"""Server-Sent Events for live viewers: `game`, `reaction`, `watchers`, `ping` every 25 s, `revoked`."""

import asyncio
import uuid
from collections.abc import AsyncIterator

from app.live.hub import LiveMessage, hub

PING_EVERY_S = 25.0


def sse(event: str, data: str, event_id: int | None = None) -> str:
    """One SSE frame."""
    head = f"event: {event}\n" + (f"id: {event_id}\n" if event_id is not None else "")
    return f"{head}data: {data}\n\n"


async def live_stream(
    game_id: uuid.UUID,
    queue: asyncio.Queue[LiveMessage],
    first: LiveMessage | None,
    ping_every_s: float = PING_EVERY_S,
) -> AsyncIterator[str]:
    """Frames for one viewer until the link is revoked or the viewer leaves."""
    try:
        if first is not None and first.data is not None:
            yield sse("game", first.data, first.rev)
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), ping_every_s)
            except TimeoutError:
                yield sse("ping", "{}")
                continue
            if message.kind == "revoked":
                yield sse("revoked", "{}")
                return
            if message.kind in ("reaction", "watchers") and message.data is not None:
                yield sse(message.kind, message.data)
            elif message.data is not None:
                yield sse("game", message.data, message.rev)
    finally:
        hub.unsubscribe(game_id, queue)
