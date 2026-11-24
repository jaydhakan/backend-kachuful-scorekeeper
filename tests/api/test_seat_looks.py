"""The worn look rides along in online seats: Spades' `who` and Ishaara's players."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WardrobeLook
from tests import ishaara_builder as ishaara
from tests import spades_tables_builder as spades
from tests.ishaara_builder import (
    ClientFor,
    client_for_fixture,  # noqa: F401 - fixture
)

LOOK = {"frame": {"id": "neon_ring", "v": 1}}


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    spades.freeze(monkeypatch)


async def test_a_spades_seat_carries_the_players_look(
    session: AsyncSession, client_for: ClientFor
) -> None:
    seats = await spades.make_seats(session, client_for)
    session.add(WardrobeLook(player_id=seats.users[0].player_id, look=LOOK))
    await session.flush()

    table = (await spades.create(seats))["table"]

    assert table["seats"][0]["who"]["look"] == LOOK


async def test_an_ishaara_seat_carries_the_players_look(
    session: AsyncSession, client_for: ClientFor
) -> None:
    table = await ishaara.make_table(session, client_for)
    session.add(WardrobeLook(player_id=table.sun.player_id, look=LOOK))
    await session.flush()

    game = (await ishaara.create(table))["game"]

    looks = {p["playerId"]: p["look"] for p in game["players"]}
    assert looks[str(table.sun.player_id)] == LOOK
    assert looks[str(table.guests[0].id)] is None
