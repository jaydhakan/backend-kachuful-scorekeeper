"""Ishaara test tables: a host, two clue-giver accounts and two guests, each with their own client."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.app import create_app
from app.db.models import Player, User
from app.domain.time import from_ms, now_utc, to_ms
from app.schemas.ishaara import CreateIshaaraIn
from app.services import ishaara_games, ishaara_state
from app.services.ishaara_games import IshaaraGamesService
from tests.conftest import ORIGIN, WRITE_HEADERS
from tests.factories import make_player, make_user, sign_in

WORDS = [
    "apple", "river", "candle", "mountain", "tiger", "window", "garden", "rocket", "pepper",
    "anchor", "violin", "desert", "planet", "bridge", "castle", "mirror", "pencil", "jungle",
    "silver", "dragon", "orange", "harbor", "forest", "button", "lantern",
]  # fmt: skip
RULES: dict[str, Any] = {"timerSec": None, "clueEntry": "typed", "packs": ["test"], "bestOf": 1}
TEAM_WORDS = {"sun": "solar", "moon": "lunar"}

ClientFor = Callable[[User], Awaitable[httpx.AsyncClient]]


class Clock:
    """The server's clock in these tests: frozen, moved by hand."""

    def __init__(self) -> None:
        self.ms = to_ms(now_utc())

    def now(self) -> Any:
        """Now as an aware datetime (what `now_utc` returns)."""
        return from_ms(self.ms)

    def advance(self, seconds: float) -> None:
        """Move the clock on."""
        self.ms += int(seconds * 1000)


def freeze(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Freeze the clock the Ishaara services read."""
    clock = Clock()
    monkeypatch.setattr(ishaara_games, "now_utc", clock.now)
    monkeypatch.setattr(ishaara_state, "now_utc", clock.now)
    return clock


@dataclass
class Table:
    """Who sits where: the host's client is the table, sun and moon are the clue phones."""

    host: User
    sun: User
    moon: User
    guests: list[Player]
    clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)

    def seats(self, *, host_seated: bool = False) -> list[dict[str, Any]]:
        """Seated players: each team a clue-giver account and a guest guesser."""
        givers = [(self.sun, "sun"), (self.moon, "moon")]
        seats = [_seat(str(u.player_id), team, "clue-giver") for u, team in givers]
        seats += [
            _seat(str(g.id), team, "guesser")
            for g, team in zip(self.guests, ("sun", "moon"), strict=True)
        ]
        if host_seated:
            seats.append(_seat(str(self.host.player_id), "sun", "guesser"))
        return seats

    def body(self, game_id: uuid.UUID | None = None, **changes: Any) -> dict[str, Any]:
        """A CreateIshaara body."""
        body = {
            "id": str(game_id or uuid.uuid4()),
            "players": self.seats(),
            "words": list(WORDS),
            "rules": dict(RULES),
        }
        return body | changes


def _seat(player_id: str, team: str, role: str) -> dict[str, Any]:
    return {
        "id": f"seat-{player_id[:8]}",
        "name": f"P-{player_id[:4]}",
        "emoji": "🦊",
        "color": "#ff8a65",
        "playerId": player_id,
        "team": team,
        "role": role,
    }


@pytest.fixture(name="client_for")
async def client_for_fixture() -> AsyncIterator[ClientFor]:
    """A signed-in client per user (each its own cookie jar), closed after the test."""
    async with AsyncExitStack() as stack:

        async def make(user: User) -> httpx.AsyncClient:
            transport = httpx.ASGITransport(app=create_app(), client=("203.0.113.9", 50000))
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=WRITE_HEADERS)
            )
            await sign_in(client, user)
            return client

        yield make


async def make_table(session: AsyncSession, client_for: ClientFor) -> Table:
    """Three accounts (host, sun and moon clue-givers) and two guests without accounts."""
    host = await make_user(session, email="host@example.com", player_name="Host")
    sun = await make_user(session, email="sun@example.com", player_name="Sunny")
    moon = await make_user(session, email="moon@example.com", player_name="Luna")
    guests = [await make_player(session, name=f"Guest {k}", created_by=host.id) for k in range(2)]
    table = Table(host=host, sun=sun, moon=moon, guests=guests)
    for name, user in (("host", host), ("sun", sun), ("moon", moon)):
        table.clients[name] = await client_for(user)
    return table


async def create(table: Table, **changes: Any) -> dict[str, Any]:
    """The host creates a game; returns the IshaaraGameOut."""
    response = await table.clients["host"].post("/api/ishaara/games", json=table.body(**changes))
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


async def start(table: Table, **changes: Any) -> tuple[dict[str, Any], list[str]]:
    """Create, then both clue-givers open their key: (state, key)."""
    game = (await create(table, **changes))["game"]
    key: list[str] = []
    for team in ("sun", "moon"):
        response = await table.clients[team].get(f"/api/ishaara/games/{game['id']}/key")
        assert response.status_code == 200, response.text
        key = response.json()["key"]
    state = await read(table, game["id"])
    assert state["status"] == "active"
    return state, key


async def read(table: Table, game_id: str, who: str = "host") -> dict[str, Any]:
    """The public state, as `who` sees it."""
    response = await table.clients[who].get(f"/api/ishaara/games/{game_id}")
    assert response.status_code == 200, response.text
    state: dict[str, Any] = response.json()["game"]
    return state


async def act(
    table: Table, who: str, state: dict[str, Any], type_: str, **fields: Any
) -> httpx.Response:
    """Send one action at the state's rev with a fresh actionId."""
    body = {"actionId": str(uuid.uuid4()), "rev": state["rev"], "type": type_, **fields}
    return await table.clients[who].post(f"/api/ishaara/games/{state['id']}/actions", json=body)


def cards_of(key: list[str], identity: str, state: dict[str, Any]) -> list[int]:
    """Face-down cards with this identity."""
    return [i for i, k in enumerate(key) if k == identity and state["revealed"][i] is None]


def hides_the_key(text: str, state: dict[str, Any], key: list[str]) -> bool:
    """No face-down identity in a public state, and the key's sequence nowhere in `text`."""
    finished = state["status"] == "finished"
    if not finished and state["solution"] is not None:
        return False
    for i, shown in enumerate(state["revealed"]):
        if shown is not None and shown != key[i]:
            return False
    for entry in state["log"]:
        if "identity" in entry and state["revealed"][entry["card"]] is None:
            return False
    compact = ",".join(f'"{k}"' for k in key)
    return finished or compact not in text.replace(" ", "")


async def service_table(session: AsyncSession) -> Table:
    """The same table, for tests that call the service directly (no clients)."""
    host = await make_user(session, email="host@example.com", player_name="Host")
    sun = await make_user(session, email="sun@example.com", player_name="Sunny")
    moon = await make_user(session, email="moon@example.com", player_name="Luna")
    guests = [await make_player(session, name=f"Guest {k}", created_by=host.id) for k in range(2)]
    return Table(host=host, sun=sun, moon=moon, guests=guests)


async def service_game(session: AsyncSession, table: Table) -> tuple[uuid.UUID, list[str]]:
    """Create through the service and open both keys: (game id, key)."""
    service = IshaaraGamesService(session)
    body = CreateIshaaraIn.model_validate(table.body())
    out, _ = await service.create(body, table.host)
    game_id = uuid.UUID(out.game["id"])
    key: list[str] = []
    for user in (table.sun, table.moon):
        key = list((await service.key(game_id, user)).key)
    return game_id, key
