"""Kachuful's ranking: equal accuracy breaks on average points per round; other metrics share."""

from app.domain.leaderboard import Metric, Tally, ranked


def _ranks(tallies: list[Tally], metric: Metric) -> list[tuple[str, int | None]]:
    return [(r.player_id, r.rank) for r in ranked(tallies, metric, min_rounds=50)]


def test_equal_accuracy_ranks_the_better_average_first() -> None:
    # Both made half their bids; b scored more per round despite fewer points in all.
    a = Tally("a", points=300, rounds=60, made=30)
    b = Tally("b", points=280, rounds=52, made=26)

    assert _ranks([a, b], Metric.ACCURACY) == [("b", 1), ("a", 2)]


def test_equal_accuracy_and_average_share_a_rank() -> None:
    a = Tally("a", points=300, rounds=60, made=30)
    b = Tally("b", points=260, rounds=52, made=26)
    c = Tally("c", points=500, rounds=60, made=20)

    assert _ranks([c, b, a], Metric.ACCURACY) == [("a", 1), ("b", 1), ("c", 3)]


def test_other_metrics_still_share_an_equal_value() -> None:
    # Same average, different accuracy: the average board keeps them level.
    a = Tally("a", points=300, rounds=60, made=40)
    b = Tally("b", points=260, rounds=52, made=20)

    assert _ranks([b, a], Metric.AVG_PER_ROUND) == [("a", 1), ("b", 1)]
