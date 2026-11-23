"""Sikke's amounts and levels against the app's `sikke-rules` fixture, and the earn rules."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain import achievements as kachuful
from app.domain.enums import GameKind, ProgressionGame
from app.domain.ishaara import achievements as ishaara
from app.domain.ishaara import awards as ishaara_awards
from app.domain.kaali_teeri import achievements as kaali_teeri
from app.domain.kaali_teeri import awards as kt_awards
from app.domain.placed import PlacedGame, PlacedSeat
from app.domain.sikke import (
    ACHIEVEMENT_SIKKE,
    BOUNTY,
    DAILY_GAME_CAP,
    FINISH,
    KURSI_DEFENCE,
    KURSI_TAKE,
    LEVEL_STARTS,
    ONLINE_BONUS,
    SITARA,
    STREAK_MAX,
    STREAK_STEP,
    WIN,
    AchievementFact,
    FinishedGame,
    SikkeEarned,
    SikkeKind,
    TajReign,
    achievement_entries,
    achievement_sikke,
    bounty_entries,
    kursi_entries,
    level_of,
    play_entries,
)
from app.domain.spades import achievements as spades
from app.domain.spades import awards as spades_awards
from app.domain.time import to_ms
from tests.domain.golden import load

TZ = ZoneInfo("Asia/Kolkata")
HOUR = 3_600_000
DAY = 24 * HOUR
WEEK = 7 * DAY
# Monday 2026-11-16, 12:00 in Asia/Kolkata.
MONDAY = to_ms(datetime(2026, 11, 16, 6, 30, tzinfo=UTC))

CATALOGUES: dict[ProgressionGame, frozenset[str]] = {
    ProgressionGame.KACHUFUL: frozenset(kachuful.CATALOGUE),
    ProgressionGame.KAALI_TEERI: kaali_teeri.MOMENT_CODES
    | kaali_teeri.GAME_CODES
    | kaali_teeri.CAREER_CODES
    | kt_awards.DAILY_CODES
    | kt_awards.SEASON_CODES,
    ProgressionGame.SPADES: spades.MOMENT_CODES
    | spades.GAME_CODES
    | spades.CAREER_CODES
    | spades_awards.DAILY_CODES
    | spades_awards.SEASON_CODES,
    ProgressionGame.ISHAARA: ishaara.MOMENT_CODES
    | ishaara.GAME_CODES
    | ishaara.CAREER_CODES
    | ishaara_awards.DAILY_CODES
    | ishaara_awards.SEASON_CODES,
}


def _game(
    k: int, at: int, *, kind: GameKind = GameKind.KACHUFUL, won: bool = False
) -> FinishedGame:
    return FinishedGame(f"g{k:03d}", kind, at, won, table_size=4)


def _of(entries: list[SikkeEarned], kind: SikkeKind) -> list[SikkeEarned]:
    return [e for e in entries if e.kind == kind]


def test_rates_match_the_app() -> None:
    rates = load("sikke-rules")["rates"]
    ours = {
        "finish": FINISH,
        "onlineBonus": ONLINE_BONUS,
        "win": WIN,
        "kursiTake": KURSI_TAKE,
        "kursiDefence": KURSI_DEFENCE,
        "streakStep": STREAK_STEP,
        "streakMax": STREAK_MAX,
        "dailyGameCap": DAILY_GAME_CAP,
        "bounty": BOUNTY,
        "sitara": SITARA,
    }
    assert ours == {k: rates[k] for k in ours}


def test_levels_match_the_app() -> None:
    levels = load("sikke-rules")["levels"]
    assert [(lv["n"], lv["from"]) for lv in levels] == list(enumerate(LEVEL_STARTS, start=1))


def test_achievement_amounts_match_the_app() -> None:
    table = load("sikke-rules")["achievements"]
    assert {g.value: codes for g, codes in ACHIEVEMENT_SIKKE.items()} == table


@pytest.mark.parametrize("game", list(ProgressionGame))
def test_every_code_a_game_can_earn_has_an_amount(game: ProgressionGame) -> None:
    assert CATALOGUES[game] - set(ACHIEVEMENT_SIKKE[game]) == set()


def test_a_code_missing_from_the_table_raises() -> None:
    with pytest.raises(ValueError, match="no Sikke amount"):
        achievement_sikke(ProgressionGame.SPADES, "hot_3")


@pytest.mark.parametrize(
    ("lifetime", "n", "start", "nxt"),
    [(0, 1, 0, 500), (499, 1, 0, 500), (500, 2, 500, 1500), (17_999, 5, 9000, 18_000)],
)
def test_level_of_a_lifetime(lifetime: int, n: int, start: int, nxt: int) -> None:
    level = level_of(lifetime)
    assert (level.n, level.from_, level.next) == (n, start, nxt)


def test_the_top_level_has_no_next() -> None:
    assert level_of(50_000).n == 6
    assert level_of(50_000).next is None


def test_achievements_pay_their_code_and_zero_codes_write_nothing() -> None:
    rows = [
        AchievementFact("a1", ProgressionGame.KACHUFUL, "hot_8", "g1", 5),
        AchievementFact("a2", ProgressionGame.KACHUFUL, "cold_8", "g1", 6),
        AchievementFact("a3", ProgressionGame.ISHAARA, "season_champion", None, 7),
    ]
    out = achievement_entries("p", rows)
    assert [(e.key, e.amount, e.game_id, e.earned_at) for e in out] == [
        ("ach:a1", 75, "g1", 5),
        ("ach:a3", 300, None, 7),
    ]


def test_a_finished_game_pays_finish_and_its_winner_win() -> None:
    out = play_entries("p", [_game(1, MONDAY, won=True)], TZ)
    assert [(e.kind, e.amount, e.key) for e in out] == [
        (SikkeKind.FINISH, 30, "finish:g001"),
        (SikkeKind.WIN, 25, "win:g001"),
        (SikkeKind.STREAK, 10, "streak:2026-W47"),
    ]


def test_online_games_pay_the_bonus() -> None:
    games = [
        _game(1, MONDAY, kind=GameKind.ISHAARA),
        _game(2, MONDAY + 1, kind=GameKind.SPADES_ONLINE),
        _game(3, MONDAY + 2, kind=GameKind.SPADES),
    ]
    finishes = _of(play_entries("p", games, TZ), SikkeKind.FINISH)
    assert [e.amount for e in finishes] == [40, 40, 30]


def test_a_two_player_table_pays_but_one_player_does_not() -> None:
    games = [
        FinishedGame("g1", GameKind.KACHUFUL, MONDAY, True, table_size=2),
        FinishedGame("g2", GameKind.KACHUFUL, MONDAY + 1, True, table_size=1),
    ]
    assert {e.game_id for e in play_entries("p", games, TZ)} == {"g1"}


def test_finish_and_win_pay_the_first_eight_games_of_one_game_a_day() -> None:
    games = [_game(k, MONDAY + k * 60_000, won=True) for k in range(10)]
    games.append(_game(99, MONDAY + 20 * 60_000, kind=GameKind.KAALI_TEERI, won=True))
    out = play_entries("p", games, TZ)
    paid = {e.game_id for e in _of(out, SikkeKind.FINISH)}
    assert paid == {f"g{k:03d}" for k in range(8)} | {"g099"}
    assert len(_of(out, SikkeKind.WIN)) == 9


def test_both_spades_kinds_share_one_daily_cap() -> None:
    games = [
        _game(k, MONDAY + k, kind=GameKind.SPADES if k % 2 else GameKind.SPADES_ONLINE)
        for k in range(9)
    ]
    assert len(_of(play_entries("p", games, TZ), SikkeKind.FINISH)) == DAILY_GAME_CAP


def test_the_cap_counts_app_tz_days() -> None:
    # 23:30 and 00:30 in Asia/Kolkata are two days, though the same UTC day.
    late = to_ms(datetime(2026, 11, 16, 18, 0, tzinfo=UTC))
    games = [_game(k, late + k) for k in range(8)] + [_game(50, late + HOUR)]
    assert len(_of(play_entries("p", games, TZ), SikkeKind.FINISH)) == 9


def test_a_streak_grows_ten_a_week_up_to_fifty_and_restarts_after_a_gap() -> None:
    weeks = [0, 1, 2, 3, 4, 5, 7]
    games = [_game(w, MONDAY + w * WEEK + 2 * DAY) for w in weeks]
    games.append(_game(90, MONDAY + 3 * DAY))  # a second game in week 0 pays no streak
    streaks = _of(play_entries("p", games, TZ), SikkeKind.STREAK)
    assert [e.amount for e in streaks] == [10, 20, 30, 40, 50, 50, 10]
    assert streaks[0].game_id == "g000"
    assert streaks[-1].data == {"weeks": 1}


def test_a_streak_runs_across_a_year_boundary() -> None:
    # ISO weeks 2026-W52, 2026-W53 and 2027-W01 (2027-01-04 is a Monday).
    days = [datetime(2026, 12, 22, 6, tzinfo=UTC), datetime(2026, 12, 29, 6, tzinfo=UTC)]
    days.append(datetime(2027, 1, 5, 6, tzinfo=UTC))
    games = [_game(k, to_ms(d)) for k, d in enumerate(days)]
    streaks = _of(play_entries("p", games, TZ), SikkeKind.STREAK)
    assert [(e.key, e.amount) for e in streaks] == [
        ("streak:2026-W52", 10),
        ("streak:2026-W53", 20),
        ("streak:2027-W01", 30),
    ]


def _kursi(k: int, places: list[tuple[str, int]]) -> PlacedGame:
    seats = tuple(PlacedSeat(p, place, seat) for seat, (p, place) in enumerate(places))
    return PlacedGame(f"k{k}", 1000 + k, seats)


def test_kursi_pays_each_take_and_each_defence() -> None:
    games = [
        _kursi(1, [("a", 1), ("b", 2), ("c", 3)]),  # a takes it first
        _kursi(2, [("a", 2), ("b", 3), ("d", 4)]),  # a defends (nobody above)
        _kursi(3, [("b", 1), ("c", 2), ("d", 3)]),  # a not seated: kept, pays nothing
        _kursi(4, [("a", 2), ("c", 1), ("d", 3)]),  # c takes it from a
        _kursi(5, [("c", 1), ("d", 2)]),  # two at the table: doesn't count
    ]
    out = kursi_entries(games)
    assert [(e.player_id, e.kind, e.amount, e.key) for e in out] == [
        ("a", SikkeKind.KURSI_TAKE, 30, "kursi:k1"),
        ("a", SikkeKind.KURSI_DEFENCE, 10, "kursi:k2"),
        ("c", SikkeKind.KURSI_TAKE, 30, "kursi:k4"),
    ]
    assert all(e.game == ProgressionGame.KACHUFUL for e in out)


DAY = 24 * 3600 * 1000
MONDAY = to_ms(datetime(2026, 11, 16, 12, tzinfo=UTC))


def _at(k: int, at: int, places: list[tuple[str, int]]) -> PlacedGame:
    seats = tuple(PlacedSeat(p, place, seat) for seat, (p, place) in enumerate(places))
    return PlacedGame(f"b{k}", at, seats)


def _bounty_rows(out: list[SikkeEarned]) -> list[tuple[str, str, str | None]]:
    return sorted((e.player_id, e.key, e.game_id) for e in out)


def test_a_bounty_pays_whoever_finishes_above_the_kursi_holder_once_a_week() -> None:
    games = [
        _at(1, MONDAY, [("a", 1), ("b", 2), ("c", 3)]),  # a takes the Kursi: no holder before
        _at(2, MONDAY + 1000, [("b", 1), ("a", 2), ("c", 3)]),  # b beats a: bounty, takes it
        _at(3, MONDAY + 2000, [("a", 1), ("b", 2), ("c", 3)]),  # a beats b: bounty on b
        _at(4, MONDAY + 3000, [("b", 1), ("a", 2), ("c", 3)]),  # b beats a again: same week
        _at(5, MONDAY + 7 * DAY, [("a", 1), ("b", 2), ("c", 3)]),  # a beats b again, new week
    ]
    created = {g.game_id: g.ended_at - 60_000 for g in games}
    out = bounty_entries(games, created, [], TZ)
    assert _bounty_rows(out) == [
        ("a", "bounty:b:2026-W47", "b3"),
        ("a", "bounty:b:2026-W48", "b5"),
        ("b", "bounty:a:2026-W47", "b2"),
    ]
    assert {(e.amount, e.kind, e.game) for e in out} == {
        (BOUNTY, SikkeKind.BOUNTY, ProgressionGame.KACHUFUL)
    }


def test_a_bounty_needs_three_seats_and_a_seated_holder() -> None:
    games = [
        _at(1, MONDAY, [("a", 1), ("b", 2), ("c", 3)]),
        _at(2, MONDAY + 1000, [("b", 1), ("a", 2)]),  # two at the table
        _at(3, MONDAY + 2000, [("b", 1), ("c", 2), ("d", 3)]),  # holder not seated
    ]
    created = {g.game_id: g.ended_at - 60_000 for g in games}
    assert bounty_entries(games, created, [], TZ) == []


def test_the_taj_at_the_games_start_has_a_bounty_too() -> None:
    games = [_at(1, MONDAY + 5000, [("c", 1), ("t", 2), ("d", 3)])]
    taj = [TajReign(("t",), MONDAY - DAY, MONDAY), TajReign(("t",), MONDAY + 1000, None)]
    early = bounty_entries(games, {"b1": MONDAY + 500}, taj, TZ)
    late = bounty_entries(games, {"b1": MONDAY + 2000}, taj, TZ)
    assert early == []
    assert _bounty_rows(late) == [("c", "bounty:t:2026-W47", "b1")]
