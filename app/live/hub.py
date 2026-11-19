"""Live fan-out: one LISTEN connection per worker feeds every SSE viewer on it (plan §15.4).

Kachuful and Kaali Teeri viewers follow share links. Server-run games follow a game directly, each
kind through the hooks it registers; signed-in users follow their own events (NOTIFY kind `user`,
routed by `userId`, never by game).

A server-run kind registers once, when its API module is imported:

    hub.register_game_kind("spades", GameKindHooks(
        loader=load_table,            # game id -> (rev, state) once per change; None once it's gone
        frame_for=table_frame,        # (state, viewer) -> one follower's `state` frame (JSON)
        events=("reaction", "ended"), # NOTIFY `data` keys sent to every follower after the state
        presence_frame=seats_frame,   # seats present (across workers) -> `seat-presence` frame
    ))
    queue = await hub.subscribe_game("spades", game_id, viewer="2")  # a seat; None = no seat
    await notify(session, game_id, "spades", data={"ended": {...}})  # delivered on commit
    await notify(session, game_id, "spades", data={"reaction": {...}, STATELESS: True})

Without `frame_for` the loader's state must already be the frame (Ishaara). Followers with a
viewer are counted per seat across workers (`seats_present`), the way share-link watchers are.
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
# A phone that reads slowly can fall this far behind before its oldest updates are dropped.
QUEUE_SIZE = 64

# NOTIFY kinds. Share links use `game`, `presence`, `reaction` and `revoked`.
ISHAARA = "ishaara"
SPADES = "spades"
USER = "user"
SEAT_PRESENCE = "seat-presence"
RESERVED_KINDS = frozenset({"game", "presence", "reaction", "revoked", USER, SEAT_PRESENCE})
# A game NOTIFY whose data carries this key skips the state reload (reactions).
STATELESS = "stateless"

# Loads (rev, event JSON) for a game, or None when it has no active live link.
EventLoader = Callable[[uuid.UUID], Awaitable[tuple[int, str] | None]]
# A follower's seat in a server-run game (as text), or None when it has none.
Viewer = str | None
GameLoader = Callable[[uuid.UUID], Awaitable[tuple[int, Any] | None]]
FrameFor = Callable[[Any, Viewer], str]
PresenceFrame = Callable[[frozenset[str]], str]


@dataclass(frozen=True)
class LiveMessage:
    """What a viewer's stream receives: a new state of the game, or the link's end."""

    kind: str
    rev: int | None = None
    data: str | None = None


@dataclass(frozen=True)
class GameKindHooks:
    """How the hub serves one server-run game kind (see the module docstring)."""

    loader: GameLoader
    frame_for: FrameFor | None = None
    events: tuple[str, ...] = ()
    presence_frame: PresenceFrame | None = None


async def notify(
    session: AsyncSession,
    game_id: uuid.UUID,
    kind: str,
    tokens: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Queue a notification; Postgres delivers it when the transaction commits."""
    payload = json.dumps({"gameId": str(game_id), "kind": kind, "tokens": tokens, "data": data})
    await _pg_notify(session, payload)


async def notify_user(
    session: AsyncSession, user_id: uuid.UUID, event: str, data: dict[str, Any]
) -> None:
    """Queue an event for one user's streams (`GET /api/me/events`); delivered on commit."""
    payload = json.dumps({"kind": USER, "userId": str(user_id), "event": event, "data": data})
    await _pg_notify(session, payload)


async def _pg_notify(session: AsyncSession, payload: str) -> None:
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload}
    )


class LiveHub:
    """Subscribers per game and per user; a game's event is loaded once and copied to each viewer."""

    def __init__(self) -> None:
        # Per game: each viewer's queue, its token, and whether it counts as a watcher.
        self._subscribers: dict[uuid.UUID, dict[asyncio.Queue[LiveMessage], tuple[str, bool]]] = {}
        # Per game: how many watchers each worker reported, so every worker can show the total.
        self._watchers: dict[uuid.UUID, dict[str, int]] = {}
        self._worker = uuid.uuid4().hex
        self._connection: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        # The LISTEN connection runs one query at a time; presence publishes take turns on it.
        self._publish_lock = asyncio.Lock()
        self._loader: EventLoader | None = None
        # Server-run games: per game, each follower's queue and viewer, and the game's kind.
        self._games: dict[uuid.UUID, dict[asyncio.Queue[LiveMessage], Viewer]] = {}
        self._game_kinds: dict[uuid.UUID, str] = {}
        self._kinds: dict[str, GameKindHooks] = {}
        # Per game: each worker's followers per seat, so every worker knows who is present.
        self._seats: dict[uuid.UUID, dict[str, dict[str, int]]] = {}
        self._users: dict[uuid.UUID, set[asyncio.Queue[LiveMessage]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def set_loader(self, loader: EventLoader) -> None:
        """How to load a game's live event (set by the public API)."""
        self._loader = loader

    def register_game_kind(self, kind: str, hooks: GameKindHooks) -> None:
        """Serve a server-run game kind's followers (NOTIFY `kind`) with these hooks."""
        if kind in RESERVED_KINDS:
            raise ValueError(f"{kind!r} is a reserved NOTIFY kind")
        self._kinds[kind] = hooks

    async def subscribe_game(
        self, kind: str, game_id: uuid.UUID, viewer: Viewer = None
    ) -> asyncio.Queue[LiveMessage]:
        """A queue that receives a server-run game's events, as seen from `viewer`'s seat."""
        await self._ensure_listening()
        queue: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._games.setdefault(game_id, {})[queue] = viewer
        self._game_kinds[game_id] = kind
        if viewer is not None:
            self._spawn(self._publish_seats(game_id))
        return queue

    def unsubscribe_game(self, game_id: uuid.UUID, queue: asyncio.Queue[LiveMessage]) -> None:
        """Forget a game's follower."""
        followers = self._games.get(game_id)
        if followers is None or queue not in followers:
            return
        viewer = followers.pop(queue)
        if not followers:
            del self._games[game_id]
            self._game_kinds.pop(game_id, None)
        if viewer is not None:
            self._spawn(self._publish_seats(game_id))

    async def subscribe_user(self, user_id: uuid.UUID) -> asyncio.Queue[LiveMessage]:
        """A queue that receives one user's own events (NOTIFY kind `user`)."""
        await self._ensure_listening()
        queue: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._users.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe_user(self, user_id: uuid.UUID, queue: asyncio.Queue[LiveMessage]) -> None:
        """Forget a user's stream."""
        queues = self._users.get(user_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                del self._users[user_id]

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

    def seats_present(self, game_id: uuid.UUID) -> frozenset[str]:
        """Seats of a game with at least one follower on any worker."""
        counts = dict(self._seats.get(game_id, {}))
        counts[self._worker] = self._seats_here(game_id)
        return frozenset(seat for per in counts.values() for seat, n in per.items() if n > 0)

    def _seats_here(self, game_id: uuid.UUID) -> dict[str, int]:
        here: dict[str, int] = {}
        for viewer in self._games.get(game_id, {}).values():
            if viewer is not None:
                here[viewer] = here.get(viewer, 0) + 1
        return here

    def _counted(self, game_id: uuid.UUID) -> int:
        return sum(1 for _, counted in self._subscribers.get(game_id, {}).values() if counted)

    async def _publish_presence(self, game_id: uuid.UUID) -> None:
        """Tell every worker how many watchers this one has for the game."""
        data = {"worker": self._worker, "n": self._counted(game_id)}
        await self._publish({"gameId": str(game_id), "kind": "presence", "data": data})

    async def _publish_seats(self, game_id: uuid.UUID) -> None:
        """Tell every worker which seats of the game this one has followers for."""
        data = {"worker": self._worker, "seats": self._seats_here(game_id)}
        await self._publish({"gameId": str(game_id), "kind": SEAT_PRESENCE, "data": data})

    async def _publish(self, message: dict[str, Any]) -> None:
        connection = self._connection
        if connection is None or connection.is_closed():
            return
        try:
            async with self._publish_lock:
                await connection.execute("SELECT pg_notify($1, $2)", CHANNEL, json.dumps(message))
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
            logger.warning("live presence not published")

    def viewer_count(self) -> int:
        """Viewers connected to this worker."""
        return (
            sum(len(v) for v in self._subscribers.values())
            + sum(len(v) for v in self._games.values())
            + sum(len(v) for v in self._users.values())
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
        if self._subscribers or self._games or self._users:
            self._spawn(self._ensure_listening())

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self._spawn(self._dispatch(json.loads(payload)))

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message["kind"]
        # A user's events carry `userId`, never a game.
        if kind == USER:
            self._dispatch_user(message)
            return
        game_id = uuid.UUID(message["gameId"])
        data = message.get("data") or {}
        if kind == SEAT_PRESENCE:
            self._dispatch_seats(game_id, data)
            return
        hooks = self._kinds.get(kind)
        if hooks is not None:
            await self._dispatch_game(game_id, hooks, data)
            return
        await self._dispatch_link(game_id, message)

    def _dispatch_user(self, message: dict[str, Any]) -> None:
        queues = self._users.get(uuid.UUID(message["userId"]))
        if not queues:
            return
        live_message = LiveMessage(kind=message["event"], data=json.dumps(message.get("data")))
        for queue in list(queues):
            _offer(queue, live_message)

    def _dispatch_seats(self, game_id: uuid.UUID, data: dict[str, Any]) -> None:
        counts = self._seats.setdefault(game_id, {})
        seats = data.get("seats") or {}
        counts[str(data.get("worker"))] = {str(seat): int(n) for seat, n in seats.items()}
        if not any(n > 0 for per in counts.values() for n in per.values()):
            del self._seats[game_id]
        hooks = self._kinds.get(self._game_kinds.get(game_id, ""))
        if hooks is None or hooks.presence_frame is None:
            return
        present = hooks.presence_frame(self.seats_present(game_id))
        for queue in list(self._games.get(game_id, {})):
            _offer(queue, LiveMessage(kind=SEAT_PRESENCE, data=present))

    async def _dispatch_link(self, game_id: uuid.UUID, message: dict[str, Any]) -> None:
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

    async def _dispatch_game(
        self, game_id: uuid.UUID, hooks: GameKindHooks, data: dict[str, Any]
    ) -> None:
        """Load the state once, then send each follower its own frame and any extra events."""
        followers = dict(self._games.get(game_id, {}))
        if not followers:
            return
        extras = [
            LiveMessage(kind=event, data=json.dumps(data[event]))
            for event in hooks.events
            if event in data
        ]
        if data.get(STATELESS):
            _offer_all(followers, extras)
            return
        loaded = await hooks.loader(game_id)
        if loaded is None:
            _offer_all(followers, [LiveMessage(kind="closed")])
            return
        rev, state = loaded
        frames: dict[Viewer, str] = {}
        for queue, viewer in followers.items():
            if viewer not in frames:
                frames[viewer] = _frame(hooks, state, viewer)
            _offer(queue, LiveMessage(kind="state", rev=rev, data=frames[viewer]))
            for extra in extras:
                _offer(queue, extra)

    async def close(self) -> None:
        """Drop the LISTEN connection (shutdown, tests)."""
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        # A new connection (and event loop) gets a fresh lock.
        self._publish_lock = asyncio.Lock()
        self._subscribers.clear()
        self._watchers.clear()
        self._games.clear()
        self._game_kinds.clear()
        self._seats.clear()
        self._users.clear()


def _frame(hooks: GameKindHooks, state: Any, viewer: Viewer) -> str:
    if hooks.frame_for is None:
        return str(state)
    return hooks.frame_for(state, viewer)


def _offer_all(
    followers: dict[asyncio.Queue[LiveMessage], Viewer], messages: list[LiveMessage]
) -> None:
    for queue in followers:
        for message in messages:
            _offer(queue, message)


def _offer(queue: asyncio.Queue[LiveMessage], message: LiveMessage) -> None:
    # `state` frames are full snapshots: a viewer that stopped reading loses its oldest messages,
    # never the newest, so it always catches up to the latest table.
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        logger.warning("live viewer queue full; dropping the oldest update")
        queue.get_nowait()
        queue.put_nowait(message)


hub = LiveHub()
