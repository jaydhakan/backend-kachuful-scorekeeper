"""Every kind's writes run its game's rules through the engine (a fake rules module stands in):
uploads return what they earned, hands and finishes recompute, deletes and abandons earn nothing,
`game_finished` follows the result, and merges recompute careers in every game."""

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, Game, User
from app.domain.enums import ProgressionGame
from app.domain.progression import Earned
from app.services.achievements import AchievementsEngine
from app.services.ishaara_games import IshaaraGamesService
from app.services.merge import MergeService
from app.services.spades_tables import SpadesTablesService
from tests.factories import make_player, make_user, sign_in
from tests.ishaara_builder import (
    ClientFor,
    Table,
    act,
    client_for_fixture,  # noqa: F401 - fixture
    make_table,
    service_game,
    service_table,
    start,
)
from tests.ishaara_builder import freeze as freeze_ishaara
from tests.kt_builder import fixture_payload
from tests.kt_builder import freeze_clock as freeze_kt
from tests.progression_fake import AT, FakeRules, swap_in
from tests.spades_builder import case, seat_registry
from tests.spades_builder import freeze_clock as freeze_spades
from tests.spades_tables_builder import Seats, bid_all, make_seats, ok, play_cards, started
from tests.spades_tables_builder import freeze as freeze_tables

KT = ProgressionGame.KAALI_TEERI
SPADES = ProgressionGame.SPADES
ISHAARA = ProgressionGame.ISHAARA


async def _rows(session: AsyncSession, game_id: str | uuid.UUID) -> list[tuple[str, str]]:
    stmt = select(Achievement.game, Achievement.code).where(
        Achievement.game_id == uuid.UUID(str(game_id))
    )
    return sorted((r[0], r[1]) for r in await session.execute(stmt))


async def _finished_items(session: AsyncSession, game_id: str | uuid.UUID) -> list[str]:
    stmt = select(Activity.game).where(
        Activity.game_id == uuid.UUID(str(game_id)), Activity.kind == "game_finished"
    )
    return list((await session.execute(stmt)).scalars())


async def _careers(session: AsyncSession, game: ProgressionGame) -> int:
    stmt = (
        select(func.count())
        .select_from(Achievement)
        .where(Achievement.game == game, Achievement.code == "t_career")
    )
    return int((await session.execute(stmt)).scalar_one())


async def _scorer(client: httpx.AsyncClient, session: AsyncSession) -> User:
    user = await make_user(session, email="scorer@example.com", player_name="Scorer")
    await sign_in(client, user)
    return user


# Kaali Teeri and the Spades scorekeeper: uploads and deletes.


async def test_a_kaali_teeri_upload_returns_what_it_earned_and_a_finished_item(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_kt(monkeypatch)
    fake = swap_in(monkeypatch, KT)
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")
    fake.players = sorted(str(p.id) for p in registry.values())

    first = await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=payload)
    again = await client.put(
        f"/api/kaali-teeri/games/{payload['syncId']}", json=payload | {"rev": payload["rev"] + 1}
    )

    assert first.status_code == 200, first.text
    earned = first.json()["earned"]
    assert sorted(e["code"] for e in earned) == ["t_career"] * 4 + ["t_moment"] * 4
    assert {e["gameKind"] for e in earned} == {"kaali-teeri"}
    assert again.json()["earned"] == []
    assert await _rows(session, payload["syncId"]) == [("kaali-teeri", "t_moment")] * 4
    assert await _finished_items(session, payload["syncId"]) == ["kaali-teeri"]
    feed = select(Activity.game).where(Activity.kind == "achievement").distinct()
    assert list((await session.execute(feed)).scalars()) == ["kaali-teeri"]


async def test_deleting_a_kaali_teeri_game_drops_its_rows_and_its_finished_item(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_kt(monkeypatch)
    fake = swap_in(monkeypatch, KT)
    await _scorer(client, session)
    payload, registry = await fixture_payload(session, "kt-4p")
    fake.players = [str(next(iter(registry.values())).id)]
    await client.put(f"/api/kaali-teeri/games/{payload['syncId']}", json=payload)

    response = await client.delete(f"/api/kaali-teeri/games/{payload['syncId']}")

    assert response.status_code == 204, response.text
    assert await _rows(session, payload["syncId"]) == []
    assert await _finished_items(session, payload["syncId"]) == []
    # Careers were recomputed for the game's players (the fake still grants them).
    assert len(fake.careers) == 2 and await _careers(session, KT) == 4


async def test_a_spades_upload_returns_what_it_earned_and_delete_takes_it_back(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_spades(monkeypatch)
    fake = swap_in(monkeypatch, SPADES)
    registry = await seat_registry(session)
    await _scorer(client, session)
    payload = case("won on target")
    fake.players = sorted(registry)[:2]

    put = await client.put(f"/api/spades/games/{payload['syncId']}", json=payload)
    finished = await _finished_items(session, payload["syncId"])
    deleted = await client.delete(f"/api/spades/games/{payload['syncId']}")

    assert put.status_code == 200, put.text
    codes = sorted((e["code"], e["gameKind"]) for e in put.json()["earned"])
    assert codes == [("t_career", "spades")] * 4 + [("t_moment", "spades")] * 2
    assert finished == ["spades"]
    assert deleted.status_code == 204
    assert await _rows(session, payload["syncId"]) == []
    assert await _finished_items(session, payload["syncId"]) == []


# Spades online: every hand, the finish, auto-end and delete.


@pytest.fixture
async def seats(
    session: AsyncSession, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> Seats:
    freeze_tables(monkeypatch)
    return await make_seats(session, client_for)


async def test_an_online_hand_ending_recomputes_the_table_and_the_finish_posts_it(
    seats: Seats, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = swap_in(monkeypatch, SPADES)
    fake.players = [str(seats.users[0].player_id)]
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    before = len(fake.games)

    await play_cards(seats)
    after_hand = await _rows(session, seats.table)
    finished_mid_game = await _finished_items(session, seats.table)
    await ok(seats, 0, "endGame")

    assert before == 0 and fake.games == [uuid.UUID(seats.table)] * 2
    assert after_hand == [("spades", "t_moment")]
    assert finished_mid_game == []
    assert await _finished_items(session, seats.table) == ["spades"]
    assert {pid for call in fake.careers for pid in call} == {u.player_id for u in seats.users}


async def test_an_idle_online_table_ends_and_a_deleted_one_earns_nothing(
    seats: Seats, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = swap_in(monkeypatch, SPADES)
    fake.players = [str(seats.users[1].player_id)]
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    await play_cards(seats)
    table = uuid.UUID(seats.table)
    await session.execute(
        update(Game).where(Game.id == table).values(updated_at=func.now() - timedelta(hours=3))
    )
    service = SpadesTablesService(session)

    assert await service.end_idle(table)
    assert await _finished_items(session, table) == ["spades"]
    await service.delete(table, seats.users[0])

    assert await _rows(session, table) == []
    assert await _finished_items(session, table) == []


# Ishaara: the finish, abandoning and delete.


async def test_an_ishaara_finish_runs_the_rules_and_posts_the_game(
    session: AsyncSession, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_ishaara(monkeypatch)
    fake = swap_in(monkeypatch, ISHAARA)
    table: Table = await make_table(session, client_for)
    fake.players = [str(table.sun.player_id)]
    state, _ = await start(table)

    resigned = await act(table, "host", state, "resign", team="sun")
    game_id = resigned.json()["game"]["id"]

    assert resigned.status_code == 200, resigned.text
    assert await _rows(session, game_id) == [("ishaara", "t_moment")]
    assert await _finished_items(session, game_id) == ["ishaara"]
    await IshaaraGamesService(session).delete(uuid.UUID(game_id), table.host)
    assert await _rows(session, game_id) == []
    assert await _finished_items(session, game_id) == []


async def test_an_abandoned_ishaara_game_earns_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = swap_in(monkeypatch, ISHAARA)
    table = await service_table(session)
    fake.players = [str(table.sun.player_id)]
    game_id, _ = await service_game(session, table)
    await session.execute(
        update(Game).where(Game.id == game_id).values(updated_at=func.now() - timedelta(hours=3))
    )

    assert await IshaaraGamesService(session).end_idle(game_id)

    assert fake.games == []
    assert await _rows(session, game_id) == []
    assert await _finished_items(session, game_id) == []


# Merges and the engine.


async def test_a_merge_recomputes_the_targets_careers_in_every_game(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes = [swap_in(monkeypatch, g) for g in (SPADES, KT, ISHAARA)]
    admin = await make_user(session, email="admin@example.com", player_name="Admin", role="admin")
    source = await make_player(session, name="Dup")
    target = await make_player(session, name="Keep")

    await MergeService(session).merge(source.id, target.id, admin)

    assert [f.careers for f in fakes] == [[{target.id}]] * 3
    for game in (SPADES, KT, ISHAARA):
        assert await _careers(session, game) == 1


class _FirstWin(FakeRules):
    """A game whose only career code is `first_win`, a code Kachuful has too."""

    async def career_earned(
        self, session: AsyncSession, player_ids: set[uuid.UUID]
    ) -> list[Earned]:
        return [Earned("first_win", str(p), None, None, None, AT) for p in player_ids]


async def test_a_kachuful_career_recompute_leaves_another_games_same_code_alone(
    session: AsyncSession,
) -> None:
    player = await make_player(session, name="Both")
    engine = AchievementsEngine(session)
    rules = _FirstWin(game=SPADES, career_codes=frozenset({"first_win"}))

    await engine.recompute_careers(rules, {player.id})
    await engine.career({player.id}, changed_from_ms=None, sequence_changed=True)

    stored = select(Achievement.game, Achievement.code).where(Achievement.player_id == player.id)
    assert [tuple(r) for r in await session.execute(stored)] == [("spades", "first_win")]


async def test_a_rules_module_earning_outside_its_codes_is_refused(session: AsyncSession) -> None:
    player = await make_player(session, name="Stray")
    rules = FakeRules(game=KT, career_codes=frozenset({"other"}))

    with pytest.raises(ValueError, match="t_career"):
        await AchievementsEngine(session).recompute_careers(rules, {player.id})
