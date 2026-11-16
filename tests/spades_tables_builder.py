"""Spades online test tables: four accounts, each with its own client, a frozen clock and a fixed
deal (seat 0 every spade, seat 1 every heart, seat 2 every diamond, seat 3 every club)."""

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.domain.spades import RANKS, legal_plays
from app.domain.time import from_ms, now_utc, to_ms
from app.services import spades_state, spades_store, spades_tables
from tests.factories import make_user
from tests.ishaara_builder import ClientFor

SUITS = ("spades", "hearts", "diamonds", "clubs")
SUIT_DEAL = [[{"rank": r, "suit": s} for r in RANKS] for s in SUITS]
# Seat 3 draws the ace: it deals, so seat 0 bids and leads first.
DRAW_TO_3 = [
    {"rank": "K", "suit": "hearts"},
    {"rank": "Q", "suit": "diamonds"},
    {"rank": "J", "suit": "clubs"},
    {"rank": "A", "suit": "spades"},
]
RULES: dict[str, Any] = {
    "target": 200,
    "timerSec": 30,
    "nil": True,
    "blindNil": False,
    "minTeamBid": None,
    "mercy": None,
}
NAMES = ("Riya", "Arjun", "Dev", "Meera")


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
    """Freeze the clock the Spades table services read, and fix the draw and the deal."""
    clock = Clock()
    for module in (spades_state, spades_store, spades_tables):
        monkeypatch.setattr(module, "now_utc", clock.now)
    monkeypatch.setattr(spades_tables, "_draw", lambda: [dict(c) for c in DRAW_TO_3])
    monkeypatch.setattr(spades_tables, "_deal", lambda: [[dict(c) for c in h] for h in SUIT_DEAL])
    return clock


@dataclass
class Seats:
    """Four accounts and their clients; `table` is the id once created."""

    users: list[User]
    clients: list[httpx.AsyncClient]
    table: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """The table's route."""
        return f"/api/spades/tables/{self.table}"


async def make_seats(session: AsyncSession, client_for: ClientFor) -> Seats:
    """Four accounts with players, all created by the first (so they are in its circle)."""
    users = [
        await make_user(session, email=f"{n.lower()}@example.com", player_name=n) for n in NAMES
    ]
    for u in users[1:]:
        await session.execute(
            update(Player).where(Player.id == u.player_id).values(created_by=users[0].id)
        )
    await session.flush()
    return Seats(users=users, clients=[await client_for(u) for u in users])


async def create(seats: Seats, **rules: Any) -> dict[str, Any]:
    """The first account opens a table; returns its view."""
    seats.table = str(uuid.uuid4())
    body = {"id": seats.table, "rules": RULES | rules}
    response = await seats.clients[0].post("/api/spades/tables", json=body)
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


async def view(seats: Seats, seat: int) -> dict[str, Any]:
    """The table as one account sees it."""
    response = await seats.clients[seat].get(seats.path)
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


async def act(
    seats: Seats, actor: int, type_: str, rev: int | None = None, **fields: Any
) -> httpx.Response:
    """One action from an account at the table's current rev (or `rev`)."""
    if rev is None:
        rev = (await view(seats, 0))["table"]["rev"]
    body = {"actionId": str(uuid.uuid4()), "rev": rev, "type": type_, **fields}
    return await seats.clients[actor].post(f"{seats.path}/actions", json=body)


async def ok(seats: Seats, actor: int, type_: str, **fields: Any) -> dict[str, Any]:
    """An action that must succeed; returns the actor's view."""
    response = await act(seats, actor, type_, **fields)
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


async def seated(seats: Seats, **rules: Any) -> dict[str, Any]:
    """A lobby with all four seated (in order) and ready."""
    await create(seats, **rules)
    for s in (1, 2, 3):
        response = await seats.clients[s].post(f"{seats.path}/join", json={})
        assert response.status_code == 200, response.text
    for s in range(4):
        await ok(seats, s, "ready")
    return await view(seats, 0)


async def started(seats: Seats, **rules: Any) -> dict[str, Any]:
    """Seated, drawn (seat 3 deals) and dealt: seat 0 to bid."""
    await seated(seats, **rules)
    await ok(seats, 0, "drawForDeal")
    return await ok(seats, 3, "deal")


async def bid_all(seats: Seats, bids: tuple[int, int, int, int] = (13, 0, 0, 0)) -> None:
    """Every seat bids in turn (seat 0 first), looking first when it must."""
    for _ in range(4):
        table = (await view(seats, 0))["table"]
        seat = table["turn"]
        if not table["looked"][seat]:
            await ok(seats, seat, "look")
        await ok(seats, seat, "bid", bid=bids[seat])


async def play_cards(seats: Seats, n: int = 52) -> dict[str, Any]:
    """Each seat to play plays its first legal card, `n` times or until the hand ends."""
    out = await view(seats, 0)
    for _ in range(n):
        table = out["table"]
        if table["phase"] != "playing" or table["status"] != "playing":
            break
        seat = table["turn"]
        mine = (await view(seats, seat))["me"]["hand"]
        card = legal_plays(mine, table["trick"], table["broken"])[0]
        out = await ok(seats, seat, "play", card=card)
    return out
