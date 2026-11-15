"""Kaali Teeri rules from plan §2 that the fixtures don't pin one by one."""

import copy
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.kaali_teeri.auction import auction
from app.domain.kaali_teeri.cards import (
    cards_each,
    default_rules,
    partners_for,
    removed_cards,
    rules_ok,
)
from app.domain.kaali_teeri.game import MISSING, KtCall, KtHand, KtRules, kt_game_from_payload
from app.domain.kaali_teeri.scoring import hand_score
from app.domain.kaali_teeri.validate import KtInvalidReason, validate
from tests.domain.golden import load
from tests.domain.test_kt_golden import KT_NOW

IDS = [f"p{i}" for i in range(6)]
RULES = KtRules(decks=1, partners=2, min_bid=150, step=5, length="endless")

REMOVED = {
    4: ([], 13),
    5: (["2clubs", "2diamonds"], 10),
    6: (["2clubs", "2diamonds", "2hearts", "2spades"], 8),
    7: (["2clubs", "2clubs", "2diamonds", "2diamonds", "2hearts", "2hearts"], 14),
    8: ([], 13),
    9: (["2clubs", "2clubs", "2diamonds", "2diamonds", "2hearts"], 11),
    10: (["2clubs", "2clubs", "2diamonds", "2diamonds"], 10),
}


@pytest.mark.parametrize("n", sorted(REMOVED))
def test_the_deck_is_trimmed_to_deal_evenly(n: int) -> None:
    cards, each = REMOVED[n]

    assert [f"{c.rank}{c.suit}" for c in removed_cards(n)] == cards
    assert cards_each(n) == each


def test_partners_and_minimum_bid_follow_the_player_count() -> None:
    assert [partners_for(n) for n in range(4, 11)] == [1, 1, 2, 2, 3, 3, 4]
    assert (default_rules(6).min_bid, default_rules(7).min_bid) == (150, 350)


def test_everyone_passing_forces_the_first_speaker_at_the_minimum() -> None:
    passes = [KtCall(player_id=p, keys=frozenset({"pass"})) for p in IDS[:4]]
    forced = KtCall(player_id="p0", keys=frozenset({"forced"}), forced=150)
    rules = KtRules(decks=1, partners=1, min_bid=150, step=5, length="endless")

    state = auction([*passes, forced], IDS[:4], rules)

    assert (state.done, state.holder, state.high, state.invalid_at) == (True, "p0", 150, None)


def _hand(bid: int, points: int, played_by: list[str]) -> KtHand:
    return KtHand(
        index=0,
        dealer_index=0,
        phase="done",
        mode="quick",
        calls=(),
        bidder="p0",
        bid=bid,
        trump="hearts",
        called=(),
        played_by=tuple(played_by),
        team_points=points,
        counted_by="defenders",
        done_at=MISSING,
    )


@given(
    bid=st.integers(30, 50).map(lambda k: k * 5),
    points=st.integers(0, 50).map(lambda k: k * 5),
    played_by=st.lists(st.sampled_from(IDS), min_size=2, max_size=2),
)
def test_nobody_goes_negative_and_a_sweep_pays_the_deck(
    bid: int, points: int, played_by: list[str]
) -> None:
    scores = hand_score(_hand(bid, points, played_by), RULES, IDS)
    team = {"p0", *played_by}

    assert all(v >= 0 for v in scores.values())
    if points == 250:
        assert {p for p, v in scores.items() if v} == team
        assert {v for v in scores.values() if v} == {250}
    elif points == 0:
        assert all(scores[p] == (0 if p in team else 250) for p in IDS)
    elif points < bid:
        assert all(scores[p] == (0 if p in team else bid) for p in IDS)


JUNK = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-1000, 1000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=8),
)
HAND_FIELDS = ("index", "dealerIndex", "phase", "mode", "bid", "teamPoints", "trump", "bidder")


@given(field=st.sampled_from(HAND_FIELDS), value=JUNK, hand=st.integers(0, 3))
def test_validation_names_a_reason_instead_of_raising(field: str, value: object, hand: int) -> None:
    game = copy.deepcopy(load("kt-4p")["game"])
    game["hands"][hand][field] = value

    found = validate(kt_game_from_payload(game), KT_NOW)

    assert found is None or found.reason in set(KtInvalidReason)


def _rules_with(n: int, partners: object) -> KtRules:
    r = default_rules(n)
    return KtRules(
        decks=r.decks, partners=partners, min_bid=r.min_bid, step=r.step, length=r.length
    )


@pytest.mark.parametrize("n", range(4, 11))
def test_the_table_calls_from_one_partner_up_to_all_but_two_players(n: int) -> None:
    assert rules_ok(_rules_with(n, 1), n)
    assert rules_ok(_rules_with(n, n - 2), n)


@pytest.mark.parametrize("partners", [0, -1, 1.5, True, False, "1", None])
def test_a_partner_count_that_is_not_a_whole_number_in_range_is_refused(partners: object) -> None:
    assert not rules_ok(_rules_with(6, partners), 6)
    assert not rules_ok(_rules_with(6, 5), 6)


def _with_partners(name: str, partners: int, called: list[dict[str, str]], seats: list[str]) -> Any:
    game = copy.deepcopy(load(name)["game"])
    game["rules"]["partners"] = partners
    for hand in game["hands"]:
        hand["called"] = copy.deepcopy(called)
        hand["playedBy"] = list(seats)
    return game


ACE, KING = {"rank": "A", "suit": "spades"}, {"rank": "K", "suit": "spades"}


def test_a_six_player_game_calling_one_partner_takes_exactly_one_card() -> None:
    one = _with_partners("kt-6p", 1, [ACE], ["p1"])
    two = _with_partners("kt-6p", 1, [ACE, KING], ["p1", "p2"])

    assert validate(kt_game_from_payload(one), KT_NOW) is None
    found = validate(kt_game_from_payload(two), KT_NOW)
    assert found is not None
    assert (found.reason, found.hand_index) == (KtInvalidReason.CALLED, 0)


def test_a_four_player_game_calling_two_partners_takes_two_cards() -> None:
    two = _with_partners("kt-4p", 2, [ACE, KING], ["p1", "p2"])

    assert validate(kt_game_from_payload(two), KT_NOW) is None
