"""Player-wide code counts Ishaara seats (platform §8 item 5), and prune-empty knows Ishaara."""

import argparse
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraGamePlayer, IshaaraPlayerDay, IshaaraRole, Player
from app.jobs import prune_empty
from app.repositories.players import players_repository
from app.schemas.ishaara import ActionIn
from app.services.ishaara_games import IshaaraGamesService
from app.services.merge import MergeService
from app.services.owned_games import MyDataService
from tests.factories import make_player, make_user
from tests.ishaara_builder import Table, service_game, service_table


async def _game(session: AsyncSession, table: Table) -> uuid.UUID:
    game_id, _ = await service_game(session, table)
    return game_id


async def test_prune_empty_deletes_ishaara_games_with_no_card_face_up(
    session: AsyncSession,
) -> None:
    table = await service_table(session)
    empty = await _game(session, table)
    played = await _game(session, table)
    await session.execute(update(Game).where(Game.id == played).values(round_count=3))

    await prune_empty.run(argparse.Namespace(dry_run=False))

    gone = await session.get(Game, empty, populate_existing=True)
    kept = await session.get(Game, played, populate_existing=True)
    assert gone is not None and gone.deleted_at is not None
    assert kept is not None and kept.deleted_at is None


async def test_a_player_who_only_played_ishaara_survives_a_data_delete(
    session: AsyncSession,
) -> None:
    table = await service_table(session)
    adder = await make_user(session, email="adder@example.com", player_name="Adder")
    seated = await make_player(session, name="Seated", created_by=adder.id)
    unused = await make_player(session, name="Unused", created_by=adder.id)
    table.guests[0] = seated
    await _game(session, table)

    deleted = await MyDataService(session).start_fresh(adder)

    assert deleted.players == 1
    assert await players_repository.get(session, seated.id) is not None
    assert await players_repository.get(session, unused.id) is None


async def test_ishaara_seats_are_counted_from_creation(session: AsyncSession) -> None:
    table = await service_table(session)
    await _game(session, table)
    await _game(session, table)

    guest = await session.get(Player, table.guests[0].id, populate_existing=True)

    assert guest is not None and guest.games_count == 2


async def test_clearing_data_deletes_hosted_ishaara_games(session: AsyncSession) -> None:
    table = await service_table(session)
    game_id = await _game(session, table)

    deleted = await MyDataService(session).start_fresh(table.host)

    row = await session.get(Game, game_id, populate_existing=True)
    assert deleted.games == 1 and row is not None and row.deleted_at is not None
    seats = await session.execute(select(func.count()).select_from(IshaaraRole))
    assert seats.scalar_one() == 0
    guest = await session.get(Player, table.sun.player_id, populate_existing=True)
    assert guest is not None and guest.games_count == 0


async def _finish(session: AsyncSession, table: Table, game_id: uuid.UUID) -> None:
    service = IshaaraGamesService(session)
    state = (await service.get(game_id, table.host)).game
    resign = ActionIn.model_validate(
        {"actionId": uuid.uuid4(), "rev": state["rev"], "type": "resign", "team": "moon"}
    )
    await service.act(game_id, resign, table.host)


async def test_merge_moves_an_ishaara_seat_everywhere(session: AsyncSession) -> None:
    table = await service_table(session)
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    keep = await make_player(session, name="Keeper")
    dupe = table.guests[0]
    game_id = await _game(session, table)
    await _finish(session, table, game_id)

    await MergeService(session).merge(dupe.id, keep.id, admin)

    row = await session.get(Game, game_id, populate_existing=True)
    assert row is not None
    ids = [p["playerId"] for p in row.payload["players"]]
    assert str(keep.id) in ids and str(dupe.id) not in ids
    assert str(keep.id) in [p["playerId"] for p in row.summary["players"]]
    for model in (IshaaraRole, IshaaraGamePlayer, IshaaraPlayerDay):
        found = await session.execute(select(model.player_id).where(model.player_id == dupe.id))
        assert found.first() is None, model
        moved = await session.execute(select(model.player_id).where(model.player_id == keep.id))
        assert moved.first() is not None, model
    kept = await session.get(Player, keep.id, populate_existing=True)
    gone = await session.get(Player, dupe.id, populate_existing=True)
    assert kept is not None and kept.games_count == 1
    assert gone is not None and gone.games_count == 0


async def test_last_played_is_the_last_card_turned(session: AsyncSession) -> None:
    table = await service_table(session)
    game_id, key = await service_game(session, table)
    service = IshaaraGamesService(session)
    state = (await service.get(game_id, table.host)).game
    giver = table.sun if state["turn"] == "sun" else table.moon
    for fields, user in (
        ({"type": "clue", "word": "solar" if giver is table.sun else "lunar", "number": 1}, giver),
        ({"type": "guess", "card": key.index(state["turn"])}, table.host),
        ({"type": "resign", "team": "sun"}, table.host),
    ):
        state = (await service.get(game_id, table.host)).game
        action = ActionIn.model_validate({"actionId": uuid.uuid4(), "rev": state["rev"], **fields})
        await service.act(game_id, action, user)

    sun = await session.get(Player, table.sun.player_id, populate_existing=True)
    row = await session.get(Game, game_id, populate_existing=True)
    assert row is not None and row.last_done_at is not None
    assert sun is not None and (sun.games_count, sun.last_played_at) == (1, row.last_done_at)
