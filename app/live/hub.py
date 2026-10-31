"""Live fan-out: one LISTEN connection per worker feeds every SSE viewer on it (plan §15.4)."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import get_logger
from app.settings import settings

logger = get_logger(__name__)

CHANNEL = "kachuful_live"
QUEUE_SIZE = 16

# Loads (rev, event JSON) for a game, or None when it has no active live link.
EventLoader = Callable[[uuid.UUID], Awaitable[tuple[int, str] | None]]


@dataclass(frozen=True)
class LiveMessage:
    """What a viewer's stream receives: a new state of the game, or the link's end."""

    kind: str
    rev: int | None = None
    data: str | None = None


async def notify(
    session: AsyncSession, game_id: uuid.UUID, kind: str, tokens: list[str] | None = None
) -> None:
    """Queue a notification; Postgres delivers it when the transaction commits."""
    payload = json.dumps({"gameId": str(game_id), "kind": kind, "tokens": tokens})
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
    )


class LiveHub:
    """Subscribers per game; a game's event is loaded once and copied to each viewer."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, dict[asyncio.Queue[LiveMessage], str]] = {}
        self._connection: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        self._loader: EventLoader | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def set_loader(self, loader: EventLoader) -> None:
        """How to load a game's live event (set by the public API)."""
        self._loader = loader

    async def subscribe(self, game_id: uuid.UUID, token: str) -> asyncio.Queue[LiveMessage]:
        """A queue that receives this game's events while the viewer stays connected."""
        await self._ensure_listening()
        queue: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(game_id, {})[queue] = token
        return queue

    def unsubscribe(self, game_id: uuid.UUID, queue: asyncio.Queue[LiveMessage]) -> None:
        """Forget a viewer."""
        viewers = self._subscribers.get(game_id)
        if viewers is not None:
            viewers.pop(queue, None)
            if not viewers:
                del self._subscribers[game_id]

    def viewer_count(self) -> int:
        """Viewers connected to this worker."""
        return sum(len(v) for v in self._subscribers.values())

    async def _ensure_listening(self) -> None:
        async with self._lock:
            if self._connection is not None and not self._connection.is_closed():
                return
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
            self._connection = await asyncpg.connect(dsn)
            await self._connection.add_listener(CHANNEL, self._on_notify)
            self._connection.add_termination_listener(self._on_lost)
            logger.info("live listener connected")

    def _on_lost(self, _: Any) -> None:
        # Postgres went away: reconnect if anyone is still watching.
        self._connection = None
        if self._subscribers:
            self._spawn(self._ensure_listening())

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self._spawn(self._dispatch(json.loads(payload)))

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        game_id = uuid.UUID(message["gameId"])
        viewers = dict(self._subscribers.get(game_id, {}))
        if not viewers:
            return
        if message["kind"] == "revoked":
            tokens = set(message.get("tokens") or [])
            for queue, token in viewers.items():
                if not tokens or token in tokens:
                    _offer(queue, LiveMessage(kind="revoked"))
            return
        if self._loader is None:
            return
        loaded = await self._loader(game_id)
        if loaded is None:
            return
        rev, data = loaded
        for queue in viewers:
            _offer(queue, LiveMessage(kind="game", rev=rev, data=data))

    async def close(self) -> None:
        """Drop the LISTEN connection (shutdown, tests)."""
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        self._subscribers.clear()


def _offer(queue: asyncio.Queue[LiveMessage], message: LiveMessage) -> None:
    # A viewer that stopped reading skips states rather than holding memory; the next one catches up.
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        logger.warning("live viewer queue full; skipping an update")


hub = LiveHub()
