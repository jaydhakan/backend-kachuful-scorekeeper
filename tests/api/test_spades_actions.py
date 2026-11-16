"""Spades online actions: replay, stale, refusals, reactions, the timer, Blind Nil, Replace with a
seat invite, the end of a game and what it derives, rematch, auto-end and the registries."""

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, Player, SpadesAction, SpadesGamePlayer, SpadesTableSeat, User
from app.security import rate_limit
from app.services.spades_tables import SpadesTablesService
from tests.factories import make_user
from tests.ishaara_builder import (
    ClientFor,
    client_for_fixture,  # noqa: F401 - fixture
)
from tests.spades_tables_builder import (
    Clock,
    Seats,
    act,
    bid_all,
    create,
    freeze,
    make_seats,
    ok,
    play_cards,
    seated,
    started,
    view,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return freeze(monkeypatch)


@pytest.fixture
async def seats(session: AsyncSession, client_for: ClientFor, clock: Clock) -> Seats:
    return await make_seats(session, client_for)


@pytest.fixture(autouse=True)
def _fresh_reactions() -> None:
    rate_limit.SPADES_REACTION._hits.clear()


def _error(response: httpx.Response, status: int, code: str, reason: str | None = None) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    if reason is not None:
        assert body["reason"] == reason
    if code in ("stale", "not_your_turn", "invalid_action"):
        assert set(body["game"]) == {"table", "me", "serverNow", "invites"}


async def _rev(seats: Seats) -> int:
    rev: int = (await view(seats, 0))["table"]["rev"]
    return rev


async def test_a_repeated_action_id_is_applied_once(seats: Seats, session: AsyncSession) -> None:
    await create(seats)
    body = {"actionId": str(uuid.uuid4()), "rev": await _rev(seats), "type": "ready"}

    first = await seats.clients[0].post(f"{seats.path}/actions", json=body)
    again = await seats.clients[0].post(f"{seats.path}/actions", json=body)

    assert first.status_code == again.status_code == 200
    assert first.json()["table"]["rev"] == again.json()["table"]["rev"] == 2
    count = select(func.count()).select_from(SpadesAction)
    assert (await session.execute(count)).scalar_one() == 1


async def test_an_old_rev_is_stale_with_the_view(seats: Seats) -> None:
    await create(seats)
    await ok(seats, 0, "ready")

    response = await act(seats, 0, "unready", rev=1)

    _error(response, 409, "stale")
    assert response.json()["game"]["table"]["rev"] == 2


async def test_refusals_carry_the_view(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    await started(seats)
    stranger = await client_for(await make_user(session, email="s@example.com", player_name="Sam"))
    rev = await _rev(seats)
    body = {"actionId": str(uuid.uuid4()), "rev": rev, "type": "bid", "bid": 3}

    out_of_turn = await act(seats, 1, "bid", bid=3)
    unseated = await stranger.post(f"{seats.path}/actions", json=body)
    too_high = await act(seats, 0, "bid", bid=14)
    not_host = await act(seats, 1, "endGame")

    _error(out_of_turn, 403, "not_your_turn")
    _error(unseated, 403, "not_your_turn")
    _error(too_high, 422, "invalid_action", "bad-bid")
    _error(not_host, 403, "not_your_turn")
    assert out_of_turn.json()["game"]["me"]["hand"] is not None
    assert unseated.json()["game"]["me"]["hand"] is None


async def test_an_unknown_action_type_is_invalid(seats: Seats) -> None:
    await create(seats)

    response = await act(seats, 0, "shuffle")

    _error(response, 422, "invalid")


async def test_a_reaction_is_sent_not_stored_and_limited(
    seats: Seats, session: AsyncSession
) -> None:
    await create(seats)
    await seats.clients[1].post(f"{seats.path}/join", json={})
    rev = await _rev(seats)

    first = await act(seats, 1, "react", rev=0, emoji="👏")
    second = await act(seats, 1, "react", rev=0, emoji="👏")
    other = await act(seats, 0, "react", emoji="🔥")
    bad = await act(seats, 0, "react", emoji="x")

    assert first.status_code == 200 and first.json()["table"]["rev"] == rev
    _error(second, 429, "rate_limited")
    assert other.status_code == 200
    _error(bad, 422, "invalid_action", "bad-emoji")
    count = select(func.count()).select_from(SpadesAction)
    assert (await session.execute(count)).scalar_one() == 0


async def test_a_full_hand_then_end_game_derives_board_rows(
    seats: Seats, session: AsyncSession
) -> None:
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    after = await play_cards(seats)
    ended = await ok(seats, 0, "endGame")

    assert after["table"]["phase"] == "handEnd"
    assert after["table"]["history"][0]["teamTricks"] == [13, 0]
    assert after["table"]["history"][0]["tricks"][0]["plays"][0]["seat"] == 0
    assert ended["table"]["status"] == "finished"
    assert ended["table"]["result"] == {"winner": "a", "endedBy": "end"}
    assert ended["me"]["hand"] is None
    rows = (
        (await session.execute(select(SpadesGamePlayer).order_by(SpadesGamePlayer.seat)))
        .scalars()
        .all()
    )
    assert [(r.seat, r.result, r.kind) for r in rows] == [
        (0, "win", "spades-online"),
        (1, "loss", "spades-online"),
        (2, "win", "spades-online"),
        (3, "loss", "spades-online"),
    ]
    assert rows[0].hands == 1 and rows[0].bids_made == 1 and rows[1].sets == 1
    done = (
        await session.execute(
            select(SpadesTableSeat.last_done_at).where(
                SpadesTableSeat.game_id == uuid.UUID(seats.table)
            )
        )
    ).scalars()
    assert all(d is not None for d in done)


async def test_a_cancelled_or_drawn_game(seats: Seats, session: AsyncSession) -> None:
    await started(seats)
    cancelled = await ok(seats, 0, "cancelGame")
    count = select(func.count()).select_from(SpadesGamePlayer)
    after_cancel = (await session.execute(count)).scalar_one()
    await started(seats)
    drawn = await ok(seats, 0, "drawGame")

    assert cancelled["table"]["status"] == "cancelled" and after_cancel == 0
    results = (await session.execute(select(SpadesGamePlayer.result))).scalars().all()
    assert drawn["table"]["result"] == {"winner": "draw", "endedBy": "draw"}
    assert results == ["draw"] * 4


async def test_blind_nil_withholds_the_hand_and_hides_an_early_declaration(seats: Seats) -> None:
    await started(seats, blindNil=True)
    before = [await view(seats, s) for s in range(4)]
    await ok(seats, 2, "blindNil")
    mine = await view(seats, 2)
    theirs = await view(seats, 0)
    looked = await ok(seats, 0, "look")

    assert all(v["me"]["hand"] is None for v in before)
    assert mine["table"]["blind"] == [False, False, True, False]
    assert theirs["table"]["blind"] == [False, False, False, False]
    assert mine["me"]["hand"] is None
    assert looked["me"]["hand"] is not None
    _error(await act(seats, 2, "look"), 422, "invalid_action", "blind-locked")


async def test_timeout_waits_for_the_deadline_then_plays_once(seats: Seats, clock: Clock) -> None:
    await started(seats)
    rev = await _rev(seats)

    early = await act(seats, 2, "timeout", rev=rev)
    clock.advance(31)
    timed = await act(seats, 2, "timeout", rev=rev)
    late = await act(seats, 3, "timeout", rev=rev)

    _error(early, 422, "invalid_action", "too-early")
    assert timed.status_code == 200
    table = timed.json()["table"]
    assert table["bids"][0] == 1 and table["turn"] == 1 and table["rev"] == rev + 1
    assert table["seats"][0]["timeouts"] == 1
    assert late.status_code == 200 and late.json()["table"]["rev"] == rev + 1


async def test_two_timeouts_mark_a_seat_away(seats: Seats, clock: Clock) -> None:
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    clock.advance(31)
    await ok(seats, 1, "timeout")
    for seat in (1, 2, 3):
        await ok(seats, seat, "play", card=(await view(seats, seat))["me"]["hand"][0])
    clock.advance(31)
    out = await ok(seats, 1, "timeout")
    for seat in (1, 2, 3):
        await ok(seats, seat, "play", card=(await view(seats, seat))["me"]["hand"][0])
    back_to_0 = (await view(seats, 1))["table"]

    assert out["table"]["seats"][0]["away"] is True
    assert out["table"]["seats"][0]["timeouts"] == 2
    assert back_to_0["turn"] == 0
    assert back_to_0["deadline"] == back_to_0["turnStartedAt"] + 5000


async def test_timer_off_times_out_only_a_seat_away_for_60_seconds(
    seats: Seats, clock: Clock, session: AsyncSession
) -> None:
    await started(seats, timerSec=None)
    clock.advance(120)
    seat0 = SpadesTableSeat.game_id == uuid.UUID(seats.table), SpadesTableSeat.seat == 0
    seen = update(SpadesTableSeat).where(*seat0)
    await session.execute(seen.values(last_seen_at=clock.now() - timedelta(seconds=10)))
    await session.flush()
    seen_lately = await act(seats, 2, "timeout")
    await session.execute(seen.values(last_seen_at=clock.now() - timedelta(minutes=5)))
    await session.flush()
    away = await act(seats, 2, "timeout")

    _error(seen_lately, 422, "invalid_action", "too-early")
    assert seen_lately.json()["game"]["table"]["deadline"] is None
    assert away.status_code == 200 and away.json()["table"]["bids"][0] == 1


async def _make_seat_away(seats: Seats, clock: Clock, seat: int = 1) -> None:
    """Seat `seat` times out twice in a row, between tricks."""
    await bid_all(seats, (5, 3, 3, 2))
    for _ in range(2):
        while (await view(seats, 0))["table"]["turn"] != seat:
            table = (await view(seats, 0))["table"]
            turn = table["turn"]
            await ok(seats, turn, "play", card=(await view(seats, turn))["me"]["hand"][0])
        clock.advance(31)
        await ok(seats, 0, "timeout")
    while (await view(seats, 0))["table"]["trick"]:
        table = (await view(seats, 0))["table"]
        turn = table["turn"]
        if turn == seat:
            clock.advance(6)
            await ok(seats, 0, "timeout")
            continue
        await ok(seats, turn, "play", card=(await view(seats, turn))["me"]["hand"][0])


async def test_replace_then_a_seat_invite_seats_the_newcomer(
    seats: Seats, clock: Clock, session: AsyncSession, client_for: ClientFor
) -> None:
    await started(seats)
    await _make_seat_away(seats, clock)
    newcomer = await make_user(session, email="new@example.com", player_name="Nia")
    newcomer_client = await client_for(newcomer)
    early = await seats.clients[0].post(
        f"{seats.path}/seat-invites", json={"userId": str(newcomer.id), "seat": "1"}
    )
    replaced = await ok(seats, 0, "replace", seat=1)
    await _circle(session, newcomer, seats)
    invite = await seats.clients[0].post(
        f"{seats.path}/seat-invites", json={"userId": str(newcomer.id), "seat": "1"}
    )
    lobby = await view(seats, 2)
    accepted = await newcomer_client.post(f"/api/seat-invites/{invite.json()['id']}/accept")
    after = (await newcomer_client.get(seats.path)).json()

    _error(early, 409, "not_in_circle")
    assert replaced["table"]["paused"] == "replace"
    assert replaced["table"]["seats"][1]["who"] is None
    assert invite.status_code == 201, invite.text
    assert invite.json()["table"]["seatsTaken"] == [0, 2, 3]
    assert invite.json()["partner"] == {"name": "Meera"}
    assert lobby["invites"][1]["userId"] == str(newcomer.id)
    assert lobby["invites"][1]["name"] == "Nia" and lobby["invites"][1]["id"] == invite.json()["id"]
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert after["me"]["seat"] == 1 and after["me"]["hand"] is not None
    assert after["table"]["paused"] is None and after["invites"] == [None] * 4
    rows = (
        await session.execute(
            select(SpadesTableSeat.player_id, SpadesTableSeat.seat).where(
                SpadesTableSeat.game_id == uuid.UUID(seats.table)
            )
        )
    ).all()
    assert (seats.users[1].player_id, None) in rows and (newcomer.player_id, 1) in rows


async def _circle(session: AsyncSession, user: User, seats: Seats) -> None:
    """Put the user in the host's circle: the host created their player."""
    await session.execute(
        update(Player).where(Player.id == user.player_id).values(created_by=seats.users[0].id)
    )
    await session.flush()


async def test_seat_invite_errors(
    seats: Seats, session: AsyncSession, client_for: ClientFor
) -> None:
    await create(seats)
    await seats.clients[1].post(f"{seats.path}/join", json={})
    path = f"{seats.path}/seat-invites"
    target = str(seats.users[2].id)

    not_host = await seats.clients[1].post(path, json={"userId": target, "seat": "2"})
    taken = await seats.clients[0].post(path, json={"userId": target, "seat": "1"})
    seated_already = await seats.clients[0].post(
        path, json={"userId": str(seats.users[1].id), "seat": "2"}
    )
    bad_seat = await seats.clients[0].post(path, json={"userId": target, "seat": "9"})
    sent = await seats.clients[0].post(path, json={"userId": target, "seat": "2"})
    shown = await view(seats, 1)
    await seats.clients[3].post(f"{seats.path}/join", json={"seat": 2})
    filled = await seats.clients[2].post(f"/api/seat-invites/{sent.json()['id']}/accept")

    _error(not_host, 403, "not_host")
    _error(taken, 409, "seat_taken")
    _error(seated_already, 409, "already_seated")
    _error(bad_seat, 422, "invalid")
    assert sent.status_code == 201 and shown["invites"][2]["userId"] == target
    _error(filled, 409, "filled")


async def test_rematch_opens_a_new_lobby(seats: Seats, session: AsyncSession) -> None:
    await started(seats)
    await ok(seats, 0, "drawGame")
    new_id = str(uuid.uuid4())

    bad = await act(seats, 0, "rematch", id="nope")
    guest = await act(seats, 1, "rematch", id=new_id)
    out = await ok(seats, 0, "rematch", id=new_id, swap=True)
    taken = await act(seats, 0, "rematch", id=seats.table)
    fresh = (await seats.clients[1].get(f"/api/spades/tables/{new_id}")).json()

    _error(bad, 422, "invalid")
    _error(guest, 403, "not_your_turn")
    assert out["table"]["next"] == new_id
    _error(taken, 409, "id_conflict")
    assert fresh["table"]["status"] == "lobby" and fresh["table"]["code"] != out["table"]["code"]
    assert fresh["table"]["seats"][0]["who"]["userId"] == str(seats.users[0].id)
    # Swapped: the host stays, the other three move one place round.
    assert fresh["me"]["seat"] == 2
    assert all(not s["ready"] for s in fresh["table"]["seats"])


async def test_auto_end_ends_idle_tables(seats: Seats, session: AsyncSession) -> None:
    await started(seats)
    await bid_all(seats, (5, 3, 3, 2))
    await play_cards(seats)
    playing = uuid.UUID(seats.table)
    await seated(seats)
    lobby = uuid.UUID(seats.table)
    await create(seats)
    recent = uuid.UUID(seats.table)
    old = Game.id.in_([playing, lobby])
    await session.execute(
        update(Game).where(old).values(updated_at=func.now() - timedelta(hours=3))
    )
    await session.flush()

    service = SpadesTablesService(session)
    ended = [await service.end_idle(g) for g in (playing, lobby, recent)]

    assert ended == [True, True, False]
    rows = {r.id: r.payload for r in (await session.execute(select(Game))).scalars()}
    assert rows[playing]["result"] == {"winner": "a", "endedBy": "end"}
    assert rows[lobby]["status"] == "cancelled" and rows[recent]["status"] == "lobby"
