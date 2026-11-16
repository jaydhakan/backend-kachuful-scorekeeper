"""Spades stats (`app/domain/spades/stats.py`): rows from the golden games, boards, pairs, head-to-head."""

import copy
from typing import Any

import pytest

from app.domain.spades.stats import (
    MIN_RATED_GAMES,
    SpGameFact,
    SpPlayerRow,
    SpTally,
    SpTotals,
    head_to_head,
    pairs,
    partners_of,
    player_rows,
    rank_board,
    rates,
    total_facts,
)
from tests.domain.golden import load

FINISHED = [
    "spades-classic",
    "spades-nil-blind",
    "spades-draw-game",
    "spades-end-game",
    "spades-mercy",
    "spades-bag-penalty",
    "spades-random-42",
]
P = [f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 6)]


def _seats(state: dict[str, Any]) -> list[str | None]:
    return [None if s["who"] is None else s["who"]["playerId"] for s in state["seats"]]


def _expected_tally(
    lines: list[dict[str, Any]], history: list[dict[str, Any]], seat: int
) -> SpTally:
    """The seat's counts worked out from the fixture's own scored lines."""
    team = "a" if seat % 2 == 0 else "b"
    out = SpTally()
    for line in lines:
        hand = next(h for h in history if h["index"] == line["index"])
        mine = line[team]
        out.hands += 1
        out.bags += mine["bags"]
        if hand["bids"][seat] == 0:
            made = any(n["seat"] == seat and n["made"] for n in mine["nils"])
            if hand["blind"][seat]:
                out.blind_nils, out.blind_nils_made = out.blind_nils + 1, out.blind_nils_made + made
            else:
                out.nils, out.nils_made = out.nils + 1, out.nils_made + made
            continue
        out.bids += 1
        out.bids_made += mine["made"] is True
        out.sets += mine["made"] is False
    return out


@pytest.mark.parametrize("name", FINISHED)
def test_rows_of_a_finished_game_match_its_lines_and_totals(name: str) -> None:
    expect = load(name)["expect"]
    state = expect["state"]
    winner = state["result"]["winner"]

    rows = {r.player_id: r for r in player_rows(state["history"], _seats(state), state["result"])}

    assert set(rows) == set(P[:4])
    for seat, pid in enumerate(P[:4]):
        row = rows[pid]
        team, other = ("a", "b") if seat % 2 == 0 else ("b", "a")
        result = "draw" if winner == "draw" else "win" if winner == team else "loss"
        assert (row.seat, row.team, row.partner_id, row.result) == (
            seat,
            team,
            P[(seat + 2) % 4],
            result,
        )
        assert (row.score, row.opp_score) == (
            expect["totals"][team]["score"],
            expect["totals"][other]["score"],
        )
        assert row.tally == _expected_tally(expect["lines"], state["history"], seat), pid


def test_nil_and_blind_nil_are_counted_apart() -> None:
    state = load("spades-nil-blind")["expect"]["state"]

    rows = {r.player_id: r for r in player_rows(state["history"], _seats(state), state["result"])}

    assert (rows[P[1]].tally.nils, rows[P[1]].tally.nils_made) == (1, 1)
    assert (rows[P[2]].tally.blind_nils, rows[P[2]].tally.blind_nils_made) == (1, 1)
    assert rows[P[2]].tally.bids == 0 and rows[P[3]].tally.sets == 1


def test_a_replaced_player_keeps_their_hands_but_no_result() -> None:
    state = copy.deepcopy(load("spades-classic")["expect"]["state"])
    # Kabir played seat 1 for the first two hands, then Arjun took over.
    for hand in state["history"][:2]:
        hand["players"][1] = P[4]

    rows = {r.player_id: r for r in player_rows(state["history"], _seats(state), state["result"])}

    assert set(rows) == set(P)
    kabir, arjun = rows[P[4]], rows[P[1]]
    assert (kabir.result, kabir.seat, kabir.team, kabir.partner_id) == (None, 1, "b", P[3])
    assert (kabir.tally.hands, arjun.tally.hands) == (2, 2)
    assert arjun.result == "win" and rows[P[3]].tally.hands == 4
    assert total_facts([_fact("g", kabir)]).games == 0


def _fact(game: str, row: SpPlayerRow) -> SpGameFact:
    return SpGameFact(
        game, row.player_id, row.team, row.partner_id, row.result, row.score, row.tally
    )


def _f(
    game: str, pid: str, partner: str, result: str | None, team: str = "a", **tally: int
) -> SpGameFact:
    return SpGameFact(game, pid, team, partner, result, 100, SpTally(**tally))


def test_totals_count_a_draw_as_played_not_won() -> None:
    facts = [
        _f("g1", "x", "y", "win", hands=3, bids=3, bids_made=2, bags=4),
        _f("g2", "x", "y", "draw", hands=2, bids=2, bids_made=2),
        _f("g3", "x", "y", "loss", hands=1, nils=1),
        _f("g4", "x", "z", None, hands=5, bids=5, sets=1),
    ]

    t = total_facts(facts)

    assert (t.games, t.wins, t.draws, t.losses, t.best_score) == (3, 1, 1, 1, 100)
    assert (t.tally.hands, t.tally.bids, t.tally.bids_made, t.tally.sets) == (11, 10, 4, 1)
    assert rates(t) == {
        "win_rate": 1 / 3,
        "made_rate": 0.4,
        "nil_rate": 0.0,
        "blind_nil_rate": None,
        "bags_per_hand": 4 / 11,
    }


def _totals(games: int, wins: int, bids: int, made: int) -> SpTotals:
    return SpTotals(games=games, wins=wins, tally=SpTally(bids=bids, bids_made=made))


def test_rank_board_shares_ranks_on_ties_and_leaves_unranked_last() -> None:
    players = {
        "a": _totals(4, 2, 10, 5),
        "b": _totals(5, 2, 10, 5),
        "c": _totals(MIN_RATED_GAMES - 1, 2, 0, 0),
        "d": _totals(3, 3, 4, 4),
    }

    wins = rank_board(players, "wins")
    rate = rank_board(players, "rate")
    made = rank_board(players, "made")

    assert [(r.player_id, r.rank) for r in wins] == [("d", 1), ("b", 2), ("a", 2), ("c", 2)]
    assert [(r.player_id, r.rank) for r in rate] == [("d", 1), ("a", 2), ("b", 3), ("c", None)]
    assert [(r.player_id, r.rank, r.value) for r in made] == [
        ("d", 1, 1.0),
        ("b", 2, 0.5),
        ("a", 2, 0.5),
        ("c", None, 0.0),
    ]


def test_pairs_and_partners_count_games_with_a_result() -> None:
    facts = [
        _f("g1", "x", "y", "win"),
        _f("g1", "y", "x", "win"),
        _f("g2", "x", "y", "draw"),
        _f("g2", "y", "x", "draw"),
        _f("g3", "x", "z", "loss"),
        _f("g3", "z", "x", "loss"),
        _f("g4", "x", "z", None),
    ]

    found = pairs(facts)
    partners = partners_of(facts, "x")

    assert [(p.a, p.b, p.games, p.wins, p.draws) for p in found] == [
        ("x", "y", 2, 1, 1),
        ("x", "z", 1, 0, 0),
    ]
    assert [(p.player_id, p.games, p.wins) for p in partners] == [("y", 2, 1), ("z", 1, 0)]


def test_head_to_head_splits_partners_and_opponents() -> None:
    facts = [
        _f("g1", "a", "b", "win", team="a"),
        _f("g1", "b", "a", "win", team="a"),
        _f("g2", "a", "c", "win", team="a"),
        _f("g2", "b", "d", "loss", team="b"),
        _f("g3", "a", "c", "draw", team="a"),
        _f("g3", "b", "d", "draw", team="b"),
        _f("g4", "a", "c", "loss", team="a"),
        _f("g4", "b", "d", None, team="b"),
        _f("g5", "a", "c", "win", team="a"),
    ]

    h = head_to_head(facts, "a", "b")

    assert (h.games, h.together, h.together_wins) == (3, 1, 1)
    assert (h.against, h.a_wins, h.b_wins, h.draws) == (2, 1, 0, 1)
