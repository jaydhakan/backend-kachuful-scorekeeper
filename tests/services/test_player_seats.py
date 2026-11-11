"""Player-wide code counts the seats of every game kind (platform §8 item 5).

A test-only seat table, registered as Ishaara's, stands in for the next game's: adding it to
`SEAT_TABLES` (and its reseat to the merge registry) must be all a new game needs.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Player, User
from app.domain.enums import GameKind
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError
from app.repositories import seats
from app.repositories.accounts import accounts_repository
from app.repositories.derived import derived_repository
from app.repositories.games import games_repository
from app.repositories.players import players_repository
from app.repositories.seats import SEAT_TABLES, SeatTable
from app.services import merge
from app.services.games import GamesService
from app.services.merge import RESEAT_BY_KIND, MergeService
from tests.factories import make_player, make_user
from tests.game_builder import MINUTE, make_payload

_metadata = MetaData()
_other_seats = Table(
    "test_other_game_seats",
    _metadata,
    Column("game_id", Uuid, nullable=False),
    Column("player_id", Uuid, nullable=False),
    Column("last_done_at", DateTime(timezone=True), nullable=False),
)
OTHER = GameKind.ISHAARA


async def _reseat_other(
    session: AsyncSession, row: Game, from_id: uuid.UUID, into_id: uuid.UUID
) -> None:
    await session.execute(
        update(_other_seats)
        .where(_other_seats.c.game_id == row.id, _other_seats.c.player_id == from_id)
        .values(player_id=into_id)
    )


@pytest.fixture
async def other_game(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Register the test seat table; its DDL rolls back with the test transaction."""
    await (await session.connection()).run_sync(_metadata.create_all)
    monkeypatch.setattr(seats, "SEAT_TABLES", (*SEAT_TABLES, SeatTable(OTHER, _other_seats)))
    monkeypatch.setattr(merge, "RESEAT_BY_KIND", {**RESEAT_BY_KIND, OTHER: _reseat_other})
    yield


async def _seat_in_other_game(
    session: AsyncSession,
    owner: User,
    players: list[Player],
    *,
    done_at: datetime,
    round_count: int = 1,
    deleted: bool = False,
) -> uuid.UUID:
    """A games row of the other kind plus its seat rows, written as that game's code would."""
    game_id, now = uuid.uuid4(), now_utc()
    session.add(
        Game(
            id=game_id,
            kind=OTHER.value,
            owner_id=owner.id,
            rev=1,
            payload={"players": [{"playerId": str(p.id)} for p in players]},
            payload_hash="x",
            status="active",
            player_count=len(players),
            round_count=round_count,
            summary={},
            created_at=now,
            client_updated_at=now,
            deleted_at=now if deleted else None,
        )
    )
    await session.flush()
    rows = [{"game_id": game_id, "player_id": p.id, "last_done_at": done_at} for p in players]
    await session.execute(insert(_other_seats), rows)
    return game_id


async def _upload_kachuful(session: AsyncSession, owner: User, players: list[Player]) -> None:
    payload = make_payload([str(p.id) for p in players], start_ms=to_ms(now_utc()) - 90 * MINUTE)
    await GamesService(session).upload(uuid.UUID(str(payload["syncId"])), payload, owner)


async def test_every_seat_table_kind_has_a_reseat_for_merges() -> None:
    assert {t.kind for t in SEAT_TABLES} <= set(RESEAT_BY_KIND)


@pytest.mark.usefixtures("other_game")
async def test_a_player_seated_only_in_another_game_survives_a_data_delete(
    session: AsyncSession,
) -> None:
    owner = await make_user(session)
    seated = await make_player(session, name="Seated", created_by=owner.id)
    unused = await make_player(session, name="Unused", created_by=owner.id)
    await _seat_in_other_game(session, owner, [seated], done_at=now_utc())

    deleted = await players_repository.delete_unused_created_by(session, owner.id)

    assert deleted == 1
    assert await session.get(Player, seated.id) is not None
    assert await players_repository.get(session, unused.id) is None
    assert await accounts_repository.player_in_use(session, seated.id) is True


@pytest.mark.usefixtures("other_game")
async def test_games_count_and_last_played_count_every_kind(session: AsyncSession) -> None:
    owner = await make_user(session)
    players = [await make_player(session, name=f"P{k}") for k in range(3)]
    await _upload_kachuful(session, owner, players)
    later = now_utc() + timedelta(minutes=5)
    await _seat_in_other_game(session, owner, players[:1], done_at=later)

    await derived_repository.refresh_player_counters(session, {p.id for p in players})

    first, second = players[0], players[1]
    await session.refresh(first)
    await session.refresh(second)
    assert (first.games_count, first.last_played_at) == (2, later)
    assert second.games_count == 1 and second.last_played_at is not None
    assert second.last_played_at < later


@pytest.mark.usefixtures("other_game")
async def test_live_games_with_a_player_come_from_every_kind(session: AsyncSession) -> None:
    owner = await make_user(session)
    player = await make_player(session, name="Everywhere")
    live = await _seat_in_other_game(session, owner, [player], done_at=now_utc())
    await _seat_in_other_game(session, owner, [player], done_at=now_utc(), deleted=True)

    assert await games_repository.ids_with_player(session, player.id) == [live]


@pytest.mark.usefixtures("other_game")
async def test_merge_moves_seats_in_another_game_and_recounts_both_players(
    session: AsyncSession,
) -> None:
    owner = await make_user(session)
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Adm")
    dupe = await make_player(session, name="Dupe")
    keep = await make_player(session, name="Keep")
    game_id = await _seat_in_other_game(session, owner, [dupe], done_at=now_utc())
    await derived_repository.refresh_player_counters(session, {dupe.id})

    await MergeService(session).merge(dupe.id, keep.id, admin)

    seated = await session.execute(
        select(_other_seats.c.player_id).where(_other_seats.c.game_id == game_id)
    )
    assert seated.scalars().all() == [keep.id]
    await session.refresh(keep)
    await session.refresh(dupe)
    assert (keep.games_count, dupe.games_count) == (1, 0)


@pytest.mark.usefixtures("other_game")
async def test_players_from_the_same_table_of_another_game_are_never_merged_or_suggested(
    session: AsyncSession,
) -> None:
    owner = await make_user(session)
    admin = await make_user(session, email="aki@example.com", role="admin", player_name="Aki")
    twin = await make_player(session, name="Zed", email="AKI@example.com")
    assert admin.player_id is not None
    aki = await players_repository.get(session, admin.player_id)
    assert aki is not None
    await _seat_in_other_game(session, owner, [aki, twin], done_at=now_utc())

    with pytest.raises(ApiError) as caught:
        await MergeService(session).merge(twin.id, aki.id, admin)
    pairs = await players_repository.possible_duplicates(session, min_similarity=0.9, limit=10)

    assert caught.value.status == 422
    assert pairs == []


@pytest.mark.usefixtures("other_game")
async def test_empty_games_are_found_per_kind(session: AsyncSession) -> None:
    owner = await make_user(session)
    player = await make_player(session, name="Idle")
    empty = await _seat_in_other_game(session, owner, [player], done_at=now_utc(), round_count=0)

    assert await games_repository.empty_ids(session, kind=OTHER) == [(empty, owner.id)]
    assert await games_repository.empty_ids(session, kind=GameKind.KACHUFUL) == []


@pytest.mark.usefixtures("other_game")
async def test_deletion_preview_sees_a_seat_in_someone_elses_game_of_another_kind(
    session: AsyncSession,
) -> None:
    member = await make_user(session)
    scorer = await make_user(session, email="scorer@example.com", player_name="Scorer")
    assert member.player_id is not None
    seated = await players_repository.get(session, member.player_id)
    assert seated is not None
    await _seat_in_other_game(session, scorer, [seated], done_at=now_utc())

    footprint = await accounts_repository.footprint(session, member)

    assert footprint.player_in_others_games is True
    assert footprint.games == 0
