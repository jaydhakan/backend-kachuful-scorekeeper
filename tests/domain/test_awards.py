"""Kachuful's season awards."""

from app.domain.awards import SEASON_METRIC, SEASON_MIN_ROUNDS, season_awards
from app.domain.leaderboard import Tally, leaderboard


def test_a_kachuful_season_no_longer_names_its_sharpest() -> None:
    month = leaderboard(
        [
            Tally("ana", points=600, rounds=60, made=50, games=6),
            Tally("bo", points=900, rounds=60, made=40, games=6),
        ],
        None,
        SEASON_METRIC,
        SEASON_MIN_ROUNDS,
    )

    codes = [
        a.code
        for a in season_awards(month, leaderboard([], None, SEASON_METRIC, SEASON_MIN_ROUNDS))
    ]

    assert "season_sharpest" not in codes
    assert codes[:2] == ["season_champion", "season_runner_up"]
