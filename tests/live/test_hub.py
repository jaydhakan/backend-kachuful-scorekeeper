"""The hub without Postgres: per-viewer frames, extra events, user routing and seat presence."""

import asyncio
import json
import uuid
from typing import Any

import pytest

from app.live.hub import (
    SEAT_PRESENCE,
    STATELESS,
    USER,
    GameKindHooks,
    LiveHub,
    LiveMessage,
    Viewer,
)

FAKE = "fake"
STATE = {"public": "table", "hands": {"0": ["AS"], "1": ["KH"]}}


class Loader:
    """A fake kind's loader: counts its calls, returns STATE (or None once the game is gone)."""

    def __init__(self, state: dict[str, Any] | None = STATE) -> None:
        self.calls = 0
        self.state = state

    async def __call__(self, _: uuid.UUID) -> tuple[int, Any] | None:
        self.calls += 1
        return None if self.state is None else (7, self.state)


def _frame_for(state: Any, viewer: Viewer) -> str:
    hand = None if viewer is None else state["hands"].get(viewer)
    return json.dumps({"table": state["public"], "hand": hand})


def _seats_frame(present: frozenset[str]) -> str:
    return json.dumps({"seats": [str(k) in present for k in range(4)]})


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


def _register(hub: LiveHub, loader: Loader) -> None:
    hooks = GameKindHooks(
        loader=loader, frame_for=_frame_for, events=("ended",), presence_frame=_seats_frame
    )
    hub.register_game_kind(FAKE, hooks)


async def test_each_follower_gets_its_own_frame_from_one_load(hub: LiveHub) -> None:
    loader = Loader()
    _register(hub, loader)
    game_id = uuid.uuid4()
    seat0 = await hub.subscribe_game(FAKE, game_id, "0")
    seat1 = await hub.subscribe_game(FAKE, game_id, "1")
    watcher = await hub.subscribe_game(FAKE, game_id)

    await hub._dispatch({"gameId": str(game_id), "kind": FAKE, "data": {"ended": {"w": 1}}})

    assert loader.calls == 1
    frames = {name: _drain(q) for name, q in (("0", seat0), ("1", seat1), ("w", watcher))}
    assert [m.kind for m in frames["0"]] == ["state", "ended"]
    assert frames["0"][0].rev == 7
    assert json.loads(frames["0"][0].data or "") == {"table": "table", "hand": ["AS"]}
    assert json.loads(frames["1"][0].data or "") == {"table": "table", "hand": ["KH"]}
    assert json.loads(frames["w"][0].data or "") == {"table": "table", "hand": None}
    assert json.loads(frames["w"][1].data or "") == {"w": 1}


async def test_a_stateless_event_skips_the_load(hub: LiveHub) -> None:
    loader = Loader()
    _register(hub, loader)
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(FAKE, game_id, "0")

    data = {"ended": {"w": 2}, STATELESS: True}
    await hub._dispatch({"gameId": str(game_id), "kind": FAKE, "data": data})

    assert loader.calls == 0
    assert [m.kind for m in _drain(queue)] == ["ended"]


async def test_a_gone_game_closes_its_followers(hub: LiveHub) -> None:
    _register(hub, Loader(state=None))
    game_id = uuid.uuid4()
    queue = await hub.subscribe_game(FAKE, game_id, "0")

    await hub._dispatch({"gameId": str(game_id), "kind": FAKE})

    assert [m.kind for m in _drain(queue)] == ["closed"]


async def test_a_game_without_frame_for_sends_the_loaded_frame_to_all(hub: LiveHub) -> None:
    async def load(_: uuid.UUID) -> tuple[int, Any]:
        return 3, '{"same": true}'

    hub.register_game_kind("plain", GameKindHooks(loader=load))
    game_id = uuid.uuid4()
    queues = [await hub.subscribe_game("plain", game_id) for _ in range(2)]

    await hub._dispatch({"gameId": str(game_id), "kind": "plain", "data": None})

    for queue in queues:
        (message,) = _drain(queue)
        assert (message.kind, message.rev, message.data) == ("state", 3, '{"same": true}')


async def test_user_events_reach_only_that_users_streams(hub: LiveHub) -> None:
    me, other = uuid.uuid4(), uuid.uuid4()
    mine = [await hub.subscribe_user(me) for _ in range(2)]
    theirs = await hub.subscribe_user(other)

    await hub._dispatch({"kind": USER, "userId": str(me), "event": "seat-invite", "data": {"a": 1}})
    await hub._dispatch({"kind": USER, "userId": str(uuid.uuid4()), "event": "x", "data": {}})

    for queue in mine:
        (message,) = _drain(queue)
        assert (message.kind, json.loads(message.data or "")) == ("seat-invite", {"a": 1})
    assert theirs.empty()
    hub.unsubscribe_user(me, mine[0])
    hub.unsubscribe_user(me, mine[1])
    assert hub.viewer_count() == 1


async def test_seat_presence_adds_up_across_workers(hub: LiveHub) -> None:
    _register(hub, Loader())
    game_id = uuid.uuid4()
    seat0 = await hub.subscribe_game(FAKE, game_id, "0")
    message = {"gameId": str(game_id), "kind": SEAT_PRESENCE}

    await hub._dispatch({**message, "data": {"worker": "other", "seats": {"2": 1}}})

    assert hub.seats_present(game_id) == {"0", "2"}
    (frame,) = _drain(seat0)
    assert json.loads(frame.data or "") == {"seats": [True, False, True, False]}
    await hub._dispatch({**message, "data": {"worker": "other", "seats": {}}})
    hub.unsubscribe_game(game_id, seat0)
    assert hub.seats_present(game_id) == frozenset()


def test_link_and_user_kinds_cannot_be_registered(hub: LiveHub) -> None:
    for kind in ("presence", "reaction", "revoked", "game", USER, SEAT_PRESENCE):
        with pytest.raises(ValueError, match="reserved"):
            hub.register_game_kind(kind, GameKindHooks(loader=Loader()))
