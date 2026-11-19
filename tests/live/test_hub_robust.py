"""The hub under pressure: ordered coalescing loads, retries, resync, probing and the table ping."""

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

import app.live.hub as hub_module
from app.live.hub import RESYNC, USER, GameKindHooks, LiveHub, LiveMessage
from app.live.sse import user_stream
from app.live.table_stream import _ping_data, table_stream

KIND = "robust"


class Gate:
    """A loader that blocks until released and returns a rising rev, so order is observable."""

    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self, _: uuid.UUID) -> tuple[int, Any]:
        self.calls += 1
        rev = self.calls
        self.started.set()
        await self.release.wait()
        return rev, {"rev": rev}


def _frame(state: Any, _: Any) -> str:
    return json.dumps(state)


async def _quiet() -> None:
    return None


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> LiveHub:
    fresh = LiveHub()
    monkeypatch.setattr(fresh, "_ensure_listening", _quiet)
    return fresh


def _drain(queue: asyncio.Queue[LiveMessage]) -> list[LiveMessage]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def _settle(hub: LiveHub) -> None:
    while hub._tasks:
        await asyncio.gather(*list(hub._tasks))


def _note(
    game_id: uuid.UUID, kind: str = KIND, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"gameId": str(game_id), "kind": kind, "data": data}


async def test_a_burst_of_notifies_loads_twice_and_delivers_in_order(hub: LiveHub) -> None:
    gate = Gate()
    hub.register_game_kind(KIND, GameKindHooks(loader=gate, frame_for=_frame))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")

    tasks = [asyncio.ensure_future(hub._dispatch(_note(game_id))) for _ in range(5)]
    await gate.started.wait()
    gate.release.set()
    await asyncio.gather(*tasks)

    assert gate.calls == 2
    revs = [m.rev for m in _drain(queue)]
    assert revs == sorted(revs) and revs[-1] == 2


async def test_extra_events_during_a_load_are_delivered_once_after_it(hub: LiveHub) -> None:
    gate = Gate()
    hub.register_game_kind(KIND, GameKindHooks(loader=gate, frame_for=_frame, events=("ended",)))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")

    first = asyncio.ensure_future(hub._dispatch(_note(game_id)))
    await gate.started.wait()
    second = asyncio.ensure_future(hub._dispatch(_note(game_id, data={"ended": {"w": 1}})))
    await asyncio.sleep(0)
    gate.release.set()
    await asyncio.gather(first, second)

    kinds = [m.kind for m in _drain(queue)]
    assert kinds == ["state", "state", "ended"]


async def test_a_state_with_a_lower_rev_than_one_already_sent_is_not_delivered(
    hub: LiveHub,
) -> None:
    revs = iter([5, 3])

    async def load(_: uuid.UUID) -> tuple[int, Any]:
        return next(revs), {}

    hub.register_game_kind(KIND, GameKindHooks(loader=load, frame_for=_frame))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")

    await hub._dispatch(_note(game_id))
    await hub._dispatch(_note(game_id))

    assert [m.rev for m in _drain(queue)] == [5]


async def test_a_failed_load_is_retried(hub: LiveHub, monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def fast(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(hub_module.asyncio, "sleep", fast)
    calls = 0

    async def load(_: uuid.UUID) -> tuple[int, Any]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("db blip")
        return 9, {}

    hub.register_game_kind(KIND, GameKindHooks(loader=load, frame_for=_frame))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")

    await hub._dispatch(_note(game_id))

    assert calls == 3
    assert [m.rev for m in _drain(queue)] == [9]


async def test_a_load_that_keeps_failing_gives_up_without_breaking_the_next_one(
    hub: LiveHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_sleep = asyncio.sleep

    async def fast(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(hub_module.asyncio, "sleep", fast)
    broken = True

    async def load(_: uuid.UUID) -> tuple[int, Any]:
        if broken:
            raise OSError("down")
        return 4, {}

    hub.register_game_kind(KIND, GameKindHooks(loader=load, frame_for=_frame))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")

    await hub._dispatch(_note(game_id))
    assert queue.empty()
    broken = False
    await hub._dispatch(_note(game_id))

    assert [m.rev for m in _drain(queue)] == [4]


async def test_a_games_bookkeeping_goes_when_its_last_follower_leaves(hub: LiveHub) -> None:
    gate = Gate()
    gate.release.set()
    hub.register_game_kind(KIND, GameKindHooks(loader=gate, frame_for=_frame))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(KIND, game_id, "0")
    await hub._dispatch(_note(game_id))
    assert game_id in hub._last_rev

    hub.unsubscribe_game(game_id, queue)
    await _settle(hub)

    assert game_id not in hub._last_rev
    assert not hub._game_runs._waiting


async def test_share_link_watchers_load_once_per_burst(hub: LiveHub) -> None:
    calls = 0
    release = asyncio.Event()

    async def load(_: uuid.UUID) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        await release.wait()
        return calls, "{}"

    hub.set_loader(load)
    game_id = uuid.uuid4()
    queue = await hub.subscribe(game_id, "tok")
    await _settle(hub)

    tasks = [
        asyncio.ensure_future(hub._dispatch({"gameId": str(game_id), "kind": "game"}))
        for _ in range(4)
    ]
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(*tasks)

    assert calls == 2
    assert [m.rev for m in _drain(queue)] == [1, 2]


async def test_live_loads_never_run_more_than_the_limit_at_once(hub: LiveHub) -> None:
    running = peak = 0

    async def load(_: uuid.UUID) -> tuple[int, Any]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return 1, {}

    hub.register_game_kind(KIND, GameKindHooks(loader=load, frame_for=_frame))
    games = [uuid.uuid4() for _ in range(30)]
    for g in games:
        await hub.subscribe_game(KIND, g, "0")

    await asyncio.gather(*(hub._dispatch(_note(g)) for g in games))

    assert 1 < peak <= hub_module.LOAD_CONCURRENCY


async def test_many_followers_all_get_the_new_state_quickly(hub: LiveHub) -> None:
    async def load(_: uuid.UUID) -> tuple[int, Any]:
        await asyncio.sleep(0.03)
        return 2, {"turn": 1}

    hub.register_game_kind(KIND, GameKindHooks(loader=load, frame_for=_frame))
    games = [uuid.uuid4() for _ in range(10)]
    queues = [await hub.subscribe_game(KIND, g, str(i % 4)) for g in games for i in range(12)]

    started = time.monotonic()
    await asyncio.gather(*(hub._dispatch(_note(g)) for g in games))

    assert time.monotonic() - started < 2.0
    assert all([m.rev for m in _drain(q)] == [2] for q in queues)


async def test_a_resync_sends_every_follower_the_state_and_ends_user_streams(
    hub: LiveHub,
) -> None:
    gate = Gate()
    gate.release.set()
    hub.register_game_kind(KIND, GameKindHooks(loader=gate, frame_for=_frame))
    game_id = uuid.uuid4()
    follower = await hub.subscribe_game(KIND, game_id, "0")
    user = await hub.subscribe_user(uuid.uuid4())

    async def link_load(_: uuid.UUID) -> tuple[int, str]:
        return 11, "{}"

    hub.set_loader(link_load)
    watcher = await hub.subscribe(game_id, "tok")

    await hub._resync()
    await _settle(hub)

    assert [m.kind for m in _drain(follower)] == ["state"]
    assert [m.kind for m in _drain(user)] == [RESYNC]
    assert [(m.kind, m.rev) for m in _drain(watcher)] == [("game", 11)]


async def test_a_user_stream_ends_on_resync() -> None:
    queue: asyncio.Queue[LiveMessage] = asyncio.Queue()
    queue.put_nowait(LiveMessage(kind=RESYNC))

    async def seen() -> None:
        return None

    frames = [f async for f in user_stream(uuid.uuid4(), queue, "{}", seen)]

    assert frames == ["event: hello\ndata: {}\n\n"]


class _Conn:
    def __init__(self, answers: bool = True, closed: bool = False) -> None:
        self.answers = answers
        self.closed = closed
        self.terminated = False

    def is_closed(self) -> bool:
        return self.closed or self.terminated

    def terminate(self) -> None:
        self.terminated = True

    async def execute(self, *_: Any) -> None:
        if not self.answers:
            await asyncio.sleep(60)


def _count_ensure(hub: LiveHub, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def ensure() -> None:
        calls.append(1)

    monkeypatch.setattr(hub, "_ensure_listening", ensure)
    return calls


async def test_a_healthy_connection_is_left_alone(
    hub: LiveHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_ensure(hub, monkeypatch)
    conn = _Conn()
    hub._connection = conn  # type: ignore[assignment]

    await hub._probe()

    assert not calls and not conn.terminated and hub._connection is conn


async def test_a_silent_connection_is_dropped_and_rebuilt(
    hub: LiveHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_module, "PROBE_TIMEOUT_S", 0.05)
    calls = _count_ensure(hub, monkeypatch)
    conn = _Conn(answers=False)
    hub._connection = conn  # type: ignore[assignment]

    await hub._probe()

    assert conn.terminated and hub._connection is None and calls == [1]


async def test_a_lost_connection_is_rebuilt_by_the_probe(
    hub: LiveHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_ensure(hub, monkeypatch)
    hub._connection = None

    await hub._probe()

    assert calls == [1]


async def test_losing_the_connection_wakes_the_monitor(hub: LiveHub) -> None:
    hub._connection = _Conn()  # type: ignore[assignment]

    hub._on_lost(None)

    assert hub._connection is None and hub._wake.is_set()


async def test_the_monitor_rebuilds_a_lost_connection_while_anyone_watches(
    hub: LiveHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_module, "PROBE_EVERY_S", 0.02)
    rebuilt = asyncio.Event()

    async def ensure() -> None:
        rebuilt.set()

    monkeypatch.setattr(hub, "_ensure_listening", ensure)
    await hub.subscribe_user(uuid.uuid4())
    task = asyncio.ensure_future(hub._monitor())
    try:
        await asyncio.wait_for(rebuilt.wait(), 2)
    finally:
        task.cancel()

    assert rebuilt.is_set()


async def test_a_message_kind_named_user_still_routes_to_users(hub: LiveHub) -> None:
    queue = await hub.subscribe_user(uuid.UUID(int=1))

    await hub._dispatch({"kind": USER, "userId": str(uuid.UUID(int=1)), "event": "e", "data": {}})

    assert [m.kind for m in _drain(queue)] == ["e"]


async def test_the_ping_carries_the_tables_rev() -> None:
    async def rev() -> int:
        return 41

    assert json.loads(await _ping_data(rev)) == {"rev": 41}


async def test_the_ping_is_plain_when_the_rev_is_unknown_slow_or_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.live.table_stream as ts

    monkeypatch.setattr(ts, "PING_LOOKUP_TIMEOUT_S", 0.05)

    async def none() -> None:
        return None

    async def slow() -> int:
        await asyncio.sleep(5)
        return 1

    async def boom() -> int:
        raise OSError("pool exhausted")

    assert [await _ping_data(f) for f in (none, slow, boom)] == ["{}", "{}", "{}"]  # type: ignore[arg-type]


async def test_the_table_stream_pings_with_the_rev() -> None:
    queue: asyncio.Queue[LiveMessage] = asyncio.Queue()

    async def rev() -> int:
        return 8

    stream = table_stream(
        uuid.uuid4(),
        queue,
        LiveMessage(kind="state"),
        send_first=False,
        on_ping=rev,
        ping_every_s=0.01,
    )
    try:
        frame = await anext(stream)
    finally:
        await stream.aclose()

    assert frame == 'event: ping\ndata: {"rev": 8}\n\n'
