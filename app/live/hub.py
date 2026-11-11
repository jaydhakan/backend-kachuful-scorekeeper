"""Live fan-out: one LISTEN connection per worker feeds every SSE viewer on it (plan §15.4).

Kachuful and Kaali Teeri viewers follow share links; Ishaara's table and clue phones follow a game
directly (NOTIFY kind `ishaara`), through their own subscriber list and loader.
"""

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
ISHAARA = "ishaara"


@dataclass(frozen=True)
class LiveMessage:
    """What a viewer's stream receives: a new state of the game, or the link's end."""

    kind: str
    rev: int | None = None
    data: str | None = None


async def notify(
    session: AsyncSession,
    game_id: uuid.UUID,
    kind: str,
    tokens: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Queue a notification; Postgres delivers it when the transaction commits."""
    payload = json.dumps({"gameId": str(game_id), "kind": kind, "tokens": tokens, "data": data})
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
    )


class LiveHub:
    """Subscribers per game; a game's event is loaded once and copied to each viewer."""

    def __init__(self) -> None:
        # Per game: each viewer's queue, its token, and whether it counts as a watcher.
        self._subscribers: dict[uuid.UUID, dict[asyncio.Queue[LiveMessage], tuple[str, bool]]] = {}
        # Per game: how many watchers each worker reported, so every worker can show the total.
        self._watchers: dict[uuid.UUID, dict[str, int]] = {}
        self._worker = uuid.uuid4().hex
        self._connection: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        self._loader: EventLoader | None = None
        # Ishaara: per game, the queues of its table and clue phones; the loader returns None once
        # the game is gone.
        self._games: dict[uuid.UUID, set[asyncio.Queue[LiveMessage]]] = {}
        self._game_loader: EventLoader | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def set_loader(self, loader: EventLoader) -> None:
        """How to load a game's live event (set by the public API)."""
        self._loader = loader

    def set_game_loader(self, loader: EventLoader) -> None:
        """How to load an Ishaara game's `state` event (set by the Ishaara API)."""
        self._game_loader = loader

    async def subscribe_game(self, game_id: uuid.UUID) -> asyncio.Queue[LiveMessage]:
        """A queue that receives an Ishaara game's events (no presence, no tokens)."""
        await self._ensure_listening()
        queue: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._games.setdefault(game_id, set()).add(queue)
        return queue

    def unsubscribe_game(self, game_id: uuid.UUID, queue: asyncio.Queue[LiveMessage]) -> None:
        """Forget an Ishaara follower."""
        followers = self._games.get(game_id)
        if followers is not None:
            followers.discard(queue)
            if not followers:
                del self._games[game_id]

    async def subscribe(
        self, game_id: uuid.UUID, token: str, *, counted: bool = True
    ) -> asyncio.Queue[LiveMessage]:
        """A queue that receives this game's events while the viewer stays connected.

        The scorer's own phone follows its table too (for reactions) but is not `counted`.
        """
        await self._ensure_listening()
        queue: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.setdefault(game_id, {})[queue] = (token, counted)
        self._spawn(self._publish_presence(game_id))
        return queue

    def unsubscribe(self, game_id: uuid.UUID, queue: asyncio.Queue[LiveMessage]) -> None:
        """Forget a viewer."""
        viewers = self._subscribers.get(game_id)
        if viewers is not None:
            viewers.pop(queue, None)
            if not viewers:
                del self._subscribers[game_id]
            self._spawn(self._publish_presence(game_id))

    def _counted(self, game_id: uuid.UUID) -> int:
        return sum(1 for _, counted in self._subscribers.get(game_id, {}).values() if counted)

    async def _publish_presence(self, game_id: uuid.UUID) -> None:
        """Tell every worker how many watchers this one has for the game."""
        payload = json.dumps(
            {
                "gameId": str(game_id),
                "kind": "presence",
                "data": {"worker": self._worker, "n": self._counted(game_id)},
            }
        )
        connection = self._connection
        if connection is None or connection.is_closed():
            return
        try:
            await connection.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
        except (asyncpg.PostgresError, OSError):
            logger.warning("live presence not published")

    def viewer_count(self) -> int:
        """Viewers connected to this worker."""
        return sum(len(v) for v in self._subscribers.values()) + sum(
            len(v) for v in self._games.values()
        )

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
        if self._subscribers or self._games:
            self._spawn(self._ensure_listening())

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self._spawn(self._dispatch(json.loads(payload)))

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        game_id = uuid.UUID(message["gameId"])
        if message["kind"] == ISHAARA:
            await self._dispatch_game(game_id, message.get("data") or {})
            return
        viewers = dict(self._subscribers.get(game_id, {}))
        if not viewers and message["kind"] != "presence":
            return
        if message["kind"] == "presence":
            data = message.get("data") or {}
            counts = self._watchers.setdefault(game_id, {})
            counts[str(data.get("worker"))] = int(data.get("n", 0))
            total = json.dumps({"n": sum(counts.values())})
            for queue in viewers:
                _offer(queue, LiveMessage(kind="watchers", data=total))
            return
        if message["kind"] == "reaction":
            data = json.dumps(message.get("data") or {})
            for queue in viewers:
                _offer(queue, LiveMessage(kind="reaction", data=data))
            return
        if message["kind"] == "revoked":
            tokens = set(message.get("tokens") or [])
            for queue, (token, _) in viewers.items():
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

    async def _dispatch_game(self, game_id: uuid.UUID, data: dict[str, Any]) -> None:
        """Load the public state once and copy it, then any `joined`/`ended`, to each follower."""
        followers = list(self._games.get(game_id, ()))
        if not followers or self._game_loader is None:
            return
        loaded = await self._game_loader(game_id)
        if loaded is None:
            for queue in followers:
                _offer(queue, LiveMessage(kind="closed"))
            return
        rev, state = loaded
        messages = [LiveMessage(kind="state", rev=rev, data=state)]
        messages += [
            LiveMessage(kind=event, data=json.dumps(data[event]))
            for event in ("joined", "ended")
            if event in data
        ]
        for queue in followers:
            for live_message in messages:
                _offer(queue, live_message)

    async def close(self) -> None:
        """Drop the LISTEN connection (shutdown, tests)."""
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        self._subscribers.clear()
        self._watchers.clear()
        self._games.clear()


def _offer(queue: asyncio.Queue[LiveMessage], message: LiveMessage) -> None:
    # A viewer that stopped reading skips states rather than holding memory; the next one catches up.
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        logger.warning("live viewer queue full; skipping an update")


hub = LiveHub()
