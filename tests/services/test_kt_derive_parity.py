"""Kaali Teeri golden fixtures through the real upload path: stored rows equal the app's."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, KtGamePlayer, KtHand
from app.domain.time import to_ms
from app.services.kt_games import KtGamesService
from tests.domain.golden import load, names
from tests.factories import make_user
from tests.kt_builder import fixture_payload, freeze_clock, key_of

VALID = [n for n in names("kt-") if not n.startswith("kt-invalid-") and n != "kt-set-stats"]


async def _upload(
    session: AsyncSession, name: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], dict[str, str], uuid.UUID]:
    freeze_clock(monkeypatch)
    payload, registry = await fixture_payload(session, name)
    owner = await make_user(session, email=f"{name}@example.com", player_name=None)
    game_id = uuid.UUID(payload["syncId"])
    await KtGamesService(session).upload(game_id, payload, owner)
    return payload, key_of(registry), game_id


@pytest.mark.parametrize("name", VALID)
async def test_stored_hands_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    payload, key, game_id = await _upload(session, name, monkeypatch)

    rows = (
        (
            await session.execute(
                select(KtHand).where(KtHand.game_id == game_id).order_by(KtHand.hand_index)
            )
        )
        .scalars()
        .all()
    )

    stored = [
        {
            "handIndex": r.hand_index,
            "dealerId": key[str(r.dealer_id)],
            "bidderId": key[str(r.bidder_id)],
            "bid": r.bid,
            "forced": r.forced,
            "trump": r.trump,
            "partnerIds": [key[str(p)] for p in r.partner_ids],
            "slots": {key[p]: v for p, v in r.slots.items()},
            "teamPoints": r.team_points,
            "made": r.made,
            "sweep": r.sweep,
            "doneAt": to_ms(r.done_at),
            "scores": {key[p]: v for p, v in r.scores.items()},
        }
        for r in rows
    ]
    # A hand without doneAt is stored at the game's updatedAt (it must count on some day).
    expected = [
        {**h, "doneAt": h["doneAt"] if h["doneAt"] is not None else payload["updatedAt"]}
        for h in load(name)["expect"]["hands"]
    ]
    assert stored == expected


@pytest.mark.parametrize("name", VALID)
async def test_stored_game_players_and_summary_match_the_app(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    payload, key, game_id = await _upload(session, name, monkeypatch)
    expect = load(name)["expect"]

    rows = (
        (
            await session.execute(
                select(KtGamePlayer)
                .where(KtGamePlayer.game_id == game_id)
                .order_by(KtGamePlayer.seat)
            )
        )
        .scalars()
        .all()
    )
    game = await session.get(Game, game_id)

    stored = [
        {
            "playerId": key[str(r.player_id)],
            "points": r.points,
            "place": r.place,
            "won": r.won,
            "handsBid": r.hands_bid,
            "bidsMade": r.bids_made,
            "timesPartner": r.times_partner,
            "partnerMade": r.partner_made,
            "doubleSlots": r.double_slots,
            "sweeps": r.sweeps,
        }
        for r in rows
    ]
    assert stored == expect["gamePlayers"]
    assert game is not None and game.kind == "kaali-teeri"
    assert game.round_count == expect["summary"]["hands"]
    summary = {
        **game.summary,
        "players": [{**p, "playerId": key[p["playerId"]]} for p in game.summary["players"]],
    }
    assert summary == {
        **expect["summary"],
        "createdAt": payload["createdAt"],
        "finishedAt": payload.get("finishedAt"),
    }
