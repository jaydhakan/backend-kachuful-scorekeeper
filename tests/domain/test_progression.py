"""The rules every game shares: ties, careers, ladders, win-rate standings and season awards."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.progression import (
    Award,
    PlayedGame,
    StandingRow,
    WinTally,
    best_of,
    climber_awards,
    iron_awards,
    is_night,
    podium_awards,
    shared_career,
    shared_ladders,
    win_rate_standings,
)
from app.domain.time import to_ms

TZ = ZoneInfo("Asia/Kolkata")


def _game(n: int, *, won: bool, others: tuple[str, ...] = ()) -> PlayedGame:
    return PlayedGame("me", f"g{n:03d}", 1_000 * n, won, ("me", *others))


def _codes(games: list[PlayedGame]) -> list[tuple[str, str]]:
    return [(e.code, e.game_id or "") for e in shared_career("me", games, games_steps=(3, 5))]


def test_best_of_shares_ties_and_respects_the_minimum() -> None:
    values = [("b", 3.0), ("a", 3.0), ("c", 1.0)]

    assert best_of(values, "x") == [Award("x", "a", 3.0), Award("x", "b", 3.0)]
    assert best_of(values, "x", lowest=True) == [Award("x", "c", 1.0)]
    assert best_of(values, "x", ok=lambda v: v > 5) == []


def test_win_runs_are_earned_at_the_game_that_reaches_them() -> None:
    results = [True, True, False, True, True, True, True, True]
    games = [_game(n, won=w) for n, w in enumerate(results)]

    codes = [c for c in _codes(games) if not c[0].startswith("games_")]

    assert codes == [
        ("first_win", "g000"),
        ("back_to_back", "g001"),
        ("back_to_back", "g004"),
        ("hat_trick", "g005"),
        ("dynasty", "g007"),
    ]


def test_games_ladder_and_crew_are_earned_when_crossed() -> None:
    games = [_game(n, won=False, others=tuple(f"p{n}{k}" for k in range(4))) for n in range(7)]

    codes = _codes(games)

    assert ("games_3", "g002") in codes and ("games_5", "g004") in codes
    # 4 new people a game: past 10 at the third game, past 25 at the seventh.
    assert ("crew_10", "g002") in codes and ("crew_25", "g006") in codes


def test_shared_ladders_count_the_longest_run_games_and_people() -> None:
    games = [
        _game(0, won=True, others=("a",)),
        _game(1, won=True, others=("a", "b")),
        _game(2, won=False),
        _game(3, won=True),
    ]

    assert shared_ladders("me", games) == {"wins": 2, "games": 4, "crew": 2}


def _ist(hour: int) -> int:
    return to_ms(datetime(2026, 11, 2, hour, 30, tzinfo=TZ))


def test_night_is_one_to_five_in_the_app_time_zone() -> None:
    assert [is_night(_ist(h), TZ) for h in (0, 1, 4, 5)] == [False, True, True, False]


def test_standings_rank_win_rate_then_games_and_share_full_ties() -> None:
    tallies = [
        WinTally("a", 10, 5),
        WinTally("b", 12, 6),
        WinTally("c", 12, 6),
        WinTally("d", 10, 8),
        WinTally("e", 3, 3),
        WinTally("f", 0, 0),
    ]

    rows = win_rate_standings(tallies, min_games=10)

    assert [(r.player_id, r.rank, r.qualified) for r in rows] == [
        ("d", 1, True),
        ("b", 2, True),
        ("c", 2, True),
        ("a", 4, True),
        ("e", None, False),
    ]
    assert rows[0].value == 0.8 and rows[0].games == 10 and rows[0].wins == 8


def test_podium_iron_and_climber_awards() -> None:
    now = win_rate_standings(
        [WinTally("a", 10, 9), WinTally("b", 14, 7), WinTally("c", 10, 5), WinTally("d", 9, 1)],
        min_games=10,
    )
    last = [
        StandingRow(1, True, "a", 1.0, 10, 10),
        StandingRow(2, True, "b", 0.5, 10, 5),
        StandingRow(3, True, "c", 0.4, 10, 4),
    ]

    assert podium_awards(now) == [
        Award("season_champion", "a", 0.9),
        Award("season_runner_up", "b", 0.5),
        Award("season_third", "c", 0.5),
    ]
    assert iron_awards(now) == [Award("season_iron", "b", 14)]
    assert climber_awards(now, last) == []
    assert climber_awards(last, now) == []
    risen = [StandingRow(1, True, "c", 1.0, 10, 10)]
    assert climber_awards(risen, last) == [Award("season_climber", "c", 2)]
