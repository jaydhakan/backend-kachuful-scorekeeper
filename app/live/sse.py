"""Server-Sent Events: live viewers (`game`, `reaction`, `watchers`, `revoked`), server-run
games' followers (`state` and the kind's events) and a user's own stream; all `ping` every 25 s."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

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


async def game_stream(
    game_id: uuid.UUID,
    queue: asyncio.Queue[LiveMessage],
    first: LiveMessage | None,
    ping_every_s: float = PING_EVERY_S,
) -> AsyncIterator[str]:
    """Frames for an Ishaara follower until the game is deleted or the follower leaves."""
    try:
        if first is not None and first.data is not None:
            yield sse("state", first.data, first.rev)
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), ping_every_s)
            except TimeoutError:
                yield sse("ping", "{}")
                continue
            if message.kind == "closed":
                return
            if message.data is not None:
                yield sse(message.kind, message.data, message.rev)
    finally:
        hub.unsubscribe_game(game_id, queue)


async def user_stream(
    user_id: uuid.UUID,
    queue: asyncio.Queue[LiveMessage],
    hello: str,
    on_ping: Callable[[], Awaitable[object]],
) -> AsyncIterator[str]:
    """Frames for one user's stream (`hello`, then their events) until they leave.

    `on_ping` runs before each ping (it marks the user seen).
    """
    try:
        yield sse("hello", hello)
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), PING_EVERY_S)
            except TimeoutError:
                await on_ping()
                yield sse("ping", "{}")
                continue
            if message.data is not None:
                yield sse(message.kind, message.data)
    finally:
        hub.unsubscribe_user(user_id, queue)
