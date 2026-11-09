"""Kaali Teeri board ranking and period days."""

from datetime import date

from app.domain.kaali_teeri.leaderboard import KtSort, KtTally, rank_board
from app.services.kt_boards import KtPeriod, period_days

TALLIES = [
    KtTally("a", games=2, wins=1, points=500, hands_bid=4, bids_made=2),
    KtTally("b", games=2, wins=1, points=700, hands_bid=2, bids_made=2),
    KtTally("c", games=1, wins=0, points=500, hands_bid=0, bids_made=0),
]


def test_points_ties_share_a_rank() -> None:
    board = rank_board(TALLIES, KtSort.POINTS)

    assert [(r.tally.player_id, r.rank) for r in board] == [("b", 1), ("a", 2), ("c", 2)]


def test_wins_ties_are_listed_by_points() -> None:
    board = rank_board(TALLIES, KtSort.WINS)

    assert [(r.tally.player_id, r.rank, r.value) for r in board] == [
        ("b", 1, 1.0),
        ("a", 1, 1.0),
        ("c", 3, 0.0),
    ]


def test_made_rate_ranks_bidders_and_leaves_the_rest_unranked() -> None:
    board = rank_board(TALLIES, KtSort.MADE)

    assert [(r.tally.player_id, r.rank, r.value) for r in board] == [
        ("b", 1, 1.0),
        ("a", 2, 0.5),
        ("c", None, 0.0),
    ]


def test_periods_are_whole_days() -> None:
    today = date(2026, 12, 31)

    assert period_days(KtPeriod.TODAY, today) == (today, date(2027, 1, 1))
    assert period_days(KtPeriod.WEEK, today) == (date(2026, 12, 25), date(2027, 1, 1))
    assert period_days(KtPeriod.SEASON, today) == (date(2026, 12, 1), date(2027, 1, 1))
    assert period_days(KtPeriod.ALL, today) == (None, None)
