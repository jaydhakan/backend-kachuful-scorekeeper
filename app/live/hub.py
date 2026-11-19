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
import contextlib
import json
import time
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
# The LISTEN connection is probed this often; a probe, a connect or a publish that takes longer than
# its timeout drops the connection, which reconnects and resyncs every follower.
PROBE_EVERY_S = 10.0
PROBE_TIMEOUT_S = 5.0
CONNECT_TIMEOUT_S = 10.0
# Live loads running at once per worker, so they can never use every pooled DB connection.
LOAD_CONCURRENCY = 8
LOAD_TRIES = 3
SLOW_LOAD_S = 0.5

# NOTIFY kinds. Share links use `game`, `presence`, `reaction` and `revoked`.
ISHAARA = "ishaara"
SPADES = "spades"
USER = "user"
SEAT_PRESENCE = "seat-presence"
# Ends a user's stream so the client reconnects and gets `hello` again (after a lost connection).
RESYNC = "resync"
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


class _Serial[C]:
    """Runs a game's work one at a time, in order; work asked for meanwhile runs once more after.

    A NOTIFY storm for one game costs one extra load, never a load per NOTIFY, and loads never overlap,
    so frames can't be delivered out of order. `batch` is what callers handed in since the last run.
    """

    def __init__(self, work: Callable[[uuid.UUID, C, list[LiveMessage]], Awaitable[None]]) -> None:
        self._work = work
        self._waiting: dict[uuid.UUID, tuple[C, list[LiveMessage]]] = {}
        self._again: set[uuid.UUID] = set()

    async def submit(self, key: uuid.UUID, context: C, batch: list[LiveMessage]) -> None:
        waiting = self._waiting.get(key)
        if waiting is not None:
            self._waiting[key] = (context, waiting[1] + batch)
            self._again.add(key)
            return
        self._waiting[key] = (context, list(batch))
        try:
            while True:
                context, pending = self._waiting[key]
                self._waiting[key] = (context, [])
                self._again.discard(key)
                try:
                    await self._work(key, context, pending)
                except Exception:
                    logger.exception("live dispatch failed")
                if key not in self._again:
                    return
        finally:
            self._waiting.pop(key, None)
            self._again.discard(key)

    def clear(self) -> None:
        self._waiting.clear()
        self._again.clear()


_FAILED: Any = object()


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
        # One load at a time per game, in order; the newest rev sent per game (frames never go back).
        self._game_runs = _Serial[GameKindHooks](self._run_game)
        self._link_runs = _Serial[None](self._run_link)
        self._last_rev: dict[uuid.UUID, int] = {}
        self._load_slots = asyncio.Semaphore(LOAD_CONCURRENCY)
        # Wakes the connection monitor when the LISTEN connection is lost.
        self._wake = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None

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
            self._last_rev.pop(game_id, None)
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
        try:
            await self._on_connection("SELECT pg_notify($1, $2)", CHANNEL, json.dumps(message))
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            logger.warning("live presence not published")

    async def _on_connection(self, query: str, *args: Any) -> None:
        """Run a query on the LISTEN connection (one at a time); a hung one drops the connection."""
        connection = self._connection
        if connection is None or connection.is_closed():
            raise ConnectionError("no live connection")
        async with self._publish_lock:
            try:
                await asyncio.wait_for(connection.execute(query, *args), PROBE_TIMEOUT_S)
            except TimeoutError:
                logger.warning("live connection did not answer; dropping it")
                connection.terminate()
                raise

    def viewer_count(self) -> int:
        """Viewers connected to this worker."""
        return (
            sum(len(v) for v in self._subscribers.values())
            + sum(len(v) for v in self._games.values())
            + sum(len(v) for v in self._users.values())
        )

    async def _ensure_listening(self) -> None:
        async with self._lock:
            self._start_monitor()
            if self._connection is not None and not self._connection.is_closed():
                return
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
            connection = await asyncpg.connect(
                dsn,
                timeout=CONNECT_TIMEOUT_S,
                server_settings={
                    "application_name": "kachuful-live",
                    # The server notices a vanished peer too, instead of holding the socket for hours.
                    "tcp_keepalives_idle": "15",
                    "tcp_keepalives_interval": "5",
                    "tcp_keepalives_count": "3",
                },
            )
            await connection.add_listener(CHANNEL, self._on_notify)
            connection.add_termination_listener(self._on_lost)
            self._connection = connection
            logger.info("live listener connected")
            # NOTIFYs sent while we were not listening are gone: bring every follower up to date.
            self._spawn(self._resync())

    def _on_lost(self, _: Any) -> None:
        # Postgres went away: the monitor reconnects if anyone is still watching.
        self._connection = None
        self._wake.set()

    def _watching(self) -> bool:
        return bool(self._subscribers or self._games or self._users)

    def _start_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.ensure_future(self._monitor())

    async def _monitor(self) -> None:
        """Probe the LISTEN connection and rebuild it, with backoff, whenever it is gone or silent."""
        failures = 0
        while True:
            delay = PROBE_EVERY_S if not failures else min(2.0**failures, PROBE_EVERY_S)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), delay)
            self._wake.clear()
            if not self._watching():
                failures = 0
                continue
            try:
                await self._probe()
                failures = 0
            except Exception:
                failures += 1
                logger.exception("live connection not restored (try %d)", failures)

    async def _probe(self) -> None:
        connection = self._connection
        if connection is not None and not connection.is_closed():
            try:
                await self._on_connection("SELECT 1")
                return
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
                logger.warning("live connection failed its probe; reconnecting")
                connection.terminate()
                self._connection = None
        await self._ensure_listening()

    async def _resync(self) -> None:
        """After (re)connecting: send each follower the current state, and end user streams.

        A user's events are not state, so a stream that may have missed one ends and the client
        reconnects (its `hello` carries what it needs).
        """
        for game_id, kind in list(self._game_kinds.items()):
            hooks = self._kinds.get(kind)
            if hooks is not None and self._games.get(game_id):
                self._spawn(self._dispatch_game(game_id, hooks, {}))
                self._spawn(self._publish_seats(game_id))
        for game_id in list(self._subscribers):
            self._spawn(self._dispatch_link(game_id, {"kind": "game"}))
            self._spawn(self._publish_presence(game_id))
        for queues in list(self._users.values()):
            for queue in list(queues):
                _offer(queue, LiveMessage(kind=RESYNC))

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
        if self._loader is not None:
            await self._link_runs.submit(game_id, None, [])

    async def _run_link(self, game_id: uuid.UUID, _: None, __: list[LiveMessage]) -> None:
        """Load a link's game once and copy it to each watcher."""
        loader = self._loader
        if loader is None or game_id not in self._subscribers:
            return
        loaded = await self._load(loader, game_id)
        if loaded is _FAILED or loaded is None:
            return
        rev, data = loaded
        for queue in dict(self._subscribers.get(game_id, {})):
            _offer(queue, LiveMessage(kind="game", rev=rev, data=data))

    async def _dispatch_game(
        self, game_id: uuid.UUID, hooks: GameKindHooks, data: dict[str, Any]
    ) -> None:
        """Send each follower its own frame and any extra events, loading the state once."""
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
        await self._game_runs.submit(game_id, hooks, extras)

    async def _run_game(
        self, game_id: uuid.UUID, hooks: GameKindHooks, extras: list[LiveMessage]
    ) -> None:
        if not self._games.get(game_id):
            return
        loaded = await self._load(hooks.loader, game_id)
        if loaded is _FAILED:
            return
        followers = dict(self._games.get(game_id, {}))
        if loaded is None:
            _offer_all(followers, [LiveMessage(kind="closed")])
            return
        rev, state = loaded
        # The loads never overlap, so a lower rev only means the row went back (never expected).
        fresh = rev >= self._last_rev.get(game_id, rev)
        if fresh:
            self._last_rev[game_id] = rev
        frames: dict[Viewer, str] = {}
        for queue, viewer in followers.items():
            if fresh:
                if viewer not in frames:
                    frames[viewer] = _frame(hooks, state, viewer)
                _offer(queue, LiveMessage(kind="state", rev=rev, data=frames[viewer]))
            for extra in extras:
                _offer(queue, extra)

    async def _load(self, loader: Callable[[uuid.UUID], Awaitable[Any]], game_id: uuid.UUID) -> Any:
        """A game's state, at most LOAD_CONCURRENCY loads at once; retried; `_FAILED` after the tries."""
        for attempt in range(LOAD_TRIES):
            started = time.monotonic()
            try:
                async with self._load_slots:
                    return await loader(game_id)
            except Exception:
                logger.exception("live load failed (try %d)", attempt + 1)
                await asyncio.sleep(0.25 * (attempt + 1))
            finally:
                took = time.monotonic() - started
                if took > SLOW_LOAD_S:
                    logger.warning("live load took %.0f ms", took * 1000)
        return _FAILED

    async def close(self) -> None:
        """Drop the LISTEN connection (shutdown, tests)."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        # A new connection (and event loop) gets fresh locks.
        self._publish_lock = asyncio.Lock()
        self._load_slots = asyncio.Semaphore(LOAD_CONCURRENCY)
        self._wake = asyncio.Event()
        self._game_runs.clear()
        self._link_runs.clear()
        self._last_rev.clear()
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
