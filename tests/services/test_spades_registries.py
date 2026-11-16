"""Both Spades kinds in the player-wide registries: deleting a user's games and merging players."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, SpadesGamePlayer
from app.domain.enums import GameKind
from app.jobs.prune_empty import KINDS
from app.services.merge import RESEAT_BY_KIND
from app.services.owned_games import DELETE_OWNED_BY_KIND, delete_owned_games
from app.services.spades_tables import SpadesTablesService
from tests.factories import make_user
from tests.ishaara_builder import (
    ClientFor,
    client_for_fixture,  # noqa: F401 - fixture
)
from tests.spades_tables_builder import (
    Clock,
    Seats,
    bid_all,
    freeze,
    make_seats,
    ok,
    play_cards,
    seated,
    started,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return freeze(monkeypatch)


@pytest.fixture
async def seats(session: AsyncSession, client_for: ClientFor, clock: Clock) -> Seats:
    return await make_seats(session, client_for)


def test_both_kinds_are_registered() -> None:
    for kind in (GameKind.SPADES, GameKind.SPADES_ONLINE):
        assert kind in RESEAT_BY_KIND and kind in DELETE_OWNED_BY_KIND and kind in KINDS


async def test_deleting_a_users_games_takes_their_open_tables(
    seats: Seats, session: AsyncSession
) -> None:
    await seated(seats)

    deleted = await delete_owned_games(session, seats.users[0], seats.users[0])

    assert deleted == 1
    row = await session.get(Game, uuid.UUID(seats.table), populate_existing=True)
    assert row is not None and row.deleted_at is not None


async def test_merge_moves_a_seat_to_another_player(seats: Seats, session: AsyncSession) -> None:
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    await play_cards(seats)
    await ok(seats, 0, "endGame")
    row = await session.get(Game, uuid.UUID(seats.table), populate_existing=True)
    assert row is not None
    other = await make_user(session, email="o@example.com", player_name="Ola")
    source = seats.users[3].player_id
    assert source is not None and other.player_id is not None

    await SpadesTablesService(session).reseat(row, source, other.player_id)

    assert row.payload["seats"][3]["who"]["playerId"] == str(other.player_id)
    assert row.payload["history"][0]["players"][3] == str(other.player_id)
    moved = select(SpadesGamePlayer.player_id).where(SpadesGamePlayer.seat == 3)
    assert (await session.execute(moved)).scalar_one() == other.player_id
