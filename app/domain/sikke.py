"""Sikke, the app's coins (rewards plan §9): what each finished game, achievement and Kursi pays.

Mirrors the app's `src/progression/sikke.ts`; parity is the `sikke-rules` golden fixture. Pure:
the service reconciles these entries with `sikke_entries` (insert new keys, update, delete gone).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.enums import GAME_OF_KIND, GameKind, ProgressionGame
from app.domain.kursi import Kursi, KursiGame, step_kursi
from app.domain.time import day_of

FINISH = 30
ONLINE_BONUS = 10
"""On top of `FINISH`, for a game every player played on their own phone."""
WIN = 25
KURSI_TAKE = 30
KURSI_DEFENCE = 10
BOUNTY = 40
"""For finishing above a holder of the Kursi or the Taj, once per holder per ISO week."""
SITARA = 30
"""For each of a closed night's Aaj ka Sitara."""
BOUNTY_MIN_TABLE = 3
STREAK_STEP = 10
STREAK_MAX = 50
DAILY_GAME_CAP = 8
"""Finishes and wins pay for this many games of one game a day; achievements always pay."""
MIN_TABLE = 2
"""A game pays `finish` and `win` (and counts for streaks) with two or more at the table."""

ONLINE_KINDS = frozenset({GameKind.ISHAARA, GameKind.SPADES_ONLINE})


class SikkeKind(StrEnum):
    """What an entry was paid for; `sikke_entries.kind` is checked against these values."""

    FINISH = "finish"
    WIN = "win"
    ACHIEVEMENT = "achievement"
    KURSI_TAKE = "kursi_take"
    KURSI_DEFENCE = "kursi_defence"
    STREAK = "streak"
    BOUNTY = "bounty"
    SITARA = "sitara"


PLAYER_KINDS = frozenset({SikkeKind.FINISH, SikkeKind.WIN, SikkeKind.ACHIEVEMENT, SikkeKind.STREAK})
"""Recomputed per player from their own games and achievements."""
PLAY_KINDS = PLAYER_KINDS - {SikkeKind.ACHIEVEMENT}
"""The player kinds that replay a player's finished games (finishes, wins, streaks)."""
KURSI_KINDS = frozenset({SikkeKind.KURSI_TAKE, SikkeKind.KURSI_DEFENCE})
"""Recomputed from Kachuful's whole Kursi chain at once."""
BOUNTY_KINDS = frozenset({SikkeKind.BOUNTY})
"""Recomputed from the Kursi chain and the Taj's reigns at once."""
SITARA_KINDS = frozenset({SikkeKind.SITARA})
"""Recomputed from the stored nights."""

# What each achievement or award code pays, per game: the fixture's numbers, never re-derived.
ACHIEVEMENT_SIKKE: dict[ProgressionGame, dict[str, int]] = {
    ProgressionGame.KACHUFUL: {
        "hot_3": 25,
        "hot_5": 50,
        "hot_8": 75,
        "hot_12": 100,
        "cold_3": 0,
        "cold_5": 0,
        "cold_8": 0,
        "cold_12": 0,
        "grand_slam": 10,
        "ninja": 5,
        "high_roller": 5,
        "peak_performer": 5,
        "lone_wolf": 5,
        "odd_one_out": 5,
        "hooked": 5,
        "dealers_revenge": 5,
        "winner": 10,
        "wire_to_wire": 10,
        "comeback": 20,
        "photo_finish": 10,
        "runaway": 10,
        "sharpshooter": 10,
        "perfect_game": 20,
        "wooden_spoon": 10,
        "zero_hero": 10,
        "marathon": 10,
        "full_house": 10,
        "night_owl": 10,
        "first_win": 25,
        "back_to_back": 50,
        "hat_trick": 75,
        "dynasty": 100,
        "spoon_collector": 25,
        "podium_regular": 25,
        "personal_best": 25,
        "games_10": 25,
        "games_50": 50,
        "games_100": 75,
        "games_250": 100,
        "games_500": 125,
        "rounds_100": 25,
        "rounds_500": 50,
        "rounds_1000": 75,
        "rounds_5000": 100,
        "made_100": 25,
        "made_500": 50,
        "made_1000": 75,
        "points_1000": 25,
        "points_5000": 50,
        "points_10000": 75,
        "crew_10": 25,
        "crew_25": 50,
        "rivalry_20": 25,
        "day_star": 15,
        "day_bang": 15,
        "day_ouch": 15,
        "day_hot": 15,
        "day_ice": 15,
        "day_sharp": 15,
        "day_grinder": 15,
        "day_hooked": 15,
        "season_champion": 300,
        "season_runner_up": 100,
        "season_third": 100,
        "season_sharpest": 100,
        "season_iron": 100,
        "season_climber": 100,
    },
    ProgressionGame.KAALI_TEERI: {
        "high_roller": 5,
        "all_in": 10,
        "clean_sweep": 10,
        "brick_wall": 10,
        "lone_wolf": 10,
        "double_agent": 5,
        "giant_killer": 5,
        "stuck_with_it": 5,
        "so_close": 0,
        "overcooked": 0,
        "winner": 10,
        "comeback": 20,
        "wire_to_wire": 10,
        "thousandaire": 10,
        "never_wrong": 10,
        "kingmaker": 10,
        "wooden_spoon": 0,
        "blanked": 0,
        "full_house": 10,
        "marathon": 10,
        "night_owl": 10,
        "first_win": 25,
        "back_to_back": 50,
        "hat_trick": 75,
        "dynasty": 100,
        "games_10": 25,
        "games_50": 50,
        "games_100": 75,
        "games_250": 100,
        "hands_100": 25,
        "hands_500": 50,
        "hands_1000": 75,
        "made_10": 25,
        "made_50": 50,
        "made_100": 75,
        "partner_10": 25,
        "partner_50": 50,
        "partner_100": 75,
        "streak_3": 25,
        "streak_5": 50,
        "streak_8": 75,
        "crew_10": 25,
        "crew_25": 50,
        "partners_in_crime": 25,
        "day_star": 15,
        "day_bid": 15,
        "day_partner": 15,
        "day_sharp": 15,
        "day_killer": 15,
        "day_overcooked": 0,
        "day_grinder": 15,
        "season_champion": 300,
        "season_runner_up": 100,
        "season_third": 100,
        "season_sharpest": 100,
        "season_kingmaker": 100,
        "season_iron": 100,
        "season_climber": 100,
    },
    ProgressionGame.SPADES: {
        "boston": 10,
        "ice_cold": 5,
        "blindside": 10,
        "double_nil": 10,
        "nil_breaker": 5,
        "set_them": 5,
        "high_roller": 5,
        "bang_on": 5,
        "sandbagged": 0,
        "blind_spot": 0,
        "last_gasp": 5,
        "deuce_ace": 10,
        "one_man_band": 5,
        "winner": 10,
        "comeback": 20,
        "exact": 20,
        "mercy": 10,
        "shutout": 20,
        "wire_to_wire": 10,
        "photo_finish": 10,
        "runaway": 10,
        "bag_collector": 0,
        "marathon": 10,
        "night_owl": 10,
        "first_win": 25,
        "back_to_back": 50,
        "hat_trick": 75,
        "dynasty": 100,
        "games_10": 25,
        "games_50": 50,
        "games_100": 75,
        "games_250": 100,
        "hands_100": 25,
        "hands_500": 50,
        "hands_1000": 75,
        "made_50": 25,
        "made_250": 50,
        "made_500": 75,
        "nils_5": 25,
        "nils_25": 50,
        "nils_50": 75,
        "crew_10": 25,
        "crew_25": 50,
        "perfect_pair": 25,
        "day_pair": 15,
        "day_sharp": 15,
        "day_nil": 15,
        "day_setter": 15,
        "day_boston": 15,
        "day_bags": 0,
        "day_grinder": 15,
        "season_champion": 300,
        "season_runner_up": 100,
        "season_third": 100,
        "season_sharpest": 100,
        "season_nil": 100,
        "season_pair": 100,
        "season_iron": 100,
        "season_climber": 100,
    },
    ProgressionGame.ISHAARA: {
        "mind_meld": 5,
        "one_word_four": 10,
        "overtime": 5,
        "trapped": 0,
        "own_goal": 0,
        "winner": 10,
        "flawless": 20,
        "speedrun": 20,
        "comeback": 20,
        "photo_finish": 10,
        "lucky_escape": 10,
        "mastermind": 10,
        "series": 10,
        "whitewash": 20,
        "white_flag": 0,
        "night_owl": 10,
        "first_win": 25,
        "back_to_back": 50,
        "hat_trick": 75,
        "dynasty": 100,
        "games_10": 25,
        "games_50": 50,
        "games_100": 75,
        "games_250": 100,
        "giver_wins_5": 25,
        "giver_wins_25": 50,
        "giver_wins_50": 75,
        "clues_25": 25,
        "clues_100": 50,
        "clues_250": 75,
        "found_50": 25,
        "found_250": 50,
        "found_1000": 75,
        "crew_10": 25,
        "crew_25": 50,
        "day_star": 15,
        "day_giver": 15,
        "day_speed": 15,
        "day_trapped": 0,
        "day_grinder": 15,
        "season_champion": 300,
        "season_runner_up": 100,
        "season_third": 100,
        "season_giver": 100,
        "season_iron": 100,
        "season_climber": 100,
    },
}


@dataclass(frozen=True)
class Level:
    """A level by lifetime Sikke: `from_` is where it starts, `next` where the next one does."""

    n: int
    from_: int
    next: int | None


LEVEL_STARTS: tuple[int, ...] = (0, 500, 1500, 4000, 9000, 18000)
"""Where levels 1-6 start; the app owns their names."""


@dataclass(frozen=True)
class FinishedGame:
    """One player's seat in a finished, non-void game of two or more players."""

    game_id: str
    kind: GameKind
    ended_at: int
    won: bool
    table_size: int


@dataclass(frozen=True)
class AchievementFact:
    """One stored achievement or award row of a player."""

    id: str
    game: ProgressionGame
    code: str
    game_id: str | None
    earned_at: int


@dataclass(frozen=True)
class SikkeEarned:
    """One entry a player should hold now; `key` is unique per player."""

    player_id: str
    key: str
    kind: SikkeKind
    amount: int
    earned_at: int
    game: ProgressionGame | None = None
    game_id: str | None = None
    achievement_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def level_of(lifetime: int) -> Level:
    """The level a lifetime total reaches."""
    i = sum(1 for start in LEVEL_STARTS[1:] if lifetime >= start)
    nxt = LEVEL_STARTS[i + 1] if i + 1 < len(LEVEL_STARTS) else None
    return Level(n=i + 1, from_=LEVEL_STARTS[i], next=nxt)


def achievement_sikke(game: ProgressionGame, code: str) -> int:
    """What one code pays; a code missing from the table is a bug, never a silent 0."""
    amount = ACHIEVEMENT_SIKKE[game].get(code)
    if amount is None:
        raise ValueError(f"no Sikke amount for {game} code {code!r}")
    return amount


def achievement_entries(player_id: str, rows: list[AchievementFact]) -> list[SikkeEarned]:
    """One entry per paying achievement row; 0-amount codes write nothing."""
    out = []
    for row in rows:
        amount = achievement_sikke(row.game, row.code)
        if amount > 0:
            out.append(
                SikkeEarned(
                    player_id=player_id,
                    key=f"ach:{row.id}",
                    kind=SikkeKind.ACHIEVEMENT,
                    amount=amount,
                    earned_at=row.earned_at,
                    game=row.game,
                    game_id=row.game_id,
                    achievement_id=row.id,
                )
            )
    return out


def _game_entry(player_id: str, g: FinishedGame, kind: SikkeKind, amount: int) -> SikkeEarned:
    return SikkeEarned(
        player_id=player_id,
        key=f"{kind}:{g.game_id}",
        kind=kind,
        amount=amount,
        earned_at=g.ended_at,
        game=GAME_OF_KIND[g.kind],
        game_id=g.game_id,
    )


def _finish_and_win(player_id: str, games: list[FinishedGame], tz: ZoneInfo) -> list[SikkeEarned]:
    """`finish` and `win` for the first `DAILY_GAME_CAP` games of one game per day."""
    played: Counter[tuple[ProgressionGame, date]] = Counter()
    out = []
    for g in games:
        day = (GAME_OF_KIND[g.kind], day_of(g.ended_at, tz))
        played[day] += 1
        if played[day] > DAILY_GAME_CAP:
            continue
        bonus = ONLINE_BONUS if g.kind in ONLINE_KINDS else 0
        out.append(_game_entry(player_id, g, SikkeKind.FINISH, FINISH + bonus))
        if g.won:
            out.append(_game_entry(player_id, g, SikkeKind.WIN, WIN))
    return out


def _week_label(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


def _streaks(player_id: str, games: list[FinishedGame], tz: ZoneInfo) -> list[SikkeEarned]:
    """The first finished game of each ISO week pays 10 per week in a row so far, up to 50."""
    firsts: dict[date, FinishedGame] = {}
    for g in games:
        day = day_of(g.ended_at, tz)
        firsts.setdefault(day - timedelta(days=day.weekday()), g)
    out = []
    run = 0
    for monday in sorted(firsts):
        run = run + 1 if monday - timedelta(days=7) in firsts else 1
        g = firsts[monday]
        out.append(
            SikkeEarned(
                player_id=player_id,
                key=f"streak:{_week_label(monday)}",
                kind=SikkeKind.STREAK,
                amount=min(STREAK_STEP * run, STREAK_MAX),
                earned_at=g.ended_at,
                game=GAME_OF_KIND[g.kind],
                game_id=g.game_id,
                data={"weeks": run},
            )
        )
    return out


def play_entries(player_id: str, games: list[FinishedGame], tz: ZoneInfo) -> list[SikkeEarned]:
    """`finish`, `win` and `streak` entries from a player's finished games of every kind."""
    ordered = sorted(
        (g for g in games if g.table_size >= MIN_TABLE), key=lambda g: (g.ended_at, g.game_id)
    )
    return _finish_and_win(player_id, ordered, tz) + _streaks(player_id, ordered, tz)


def _kursi_entry(player_id: str, g: KursiGame, kind: SikkeKind) -> SikkeEarned:
    amount = KURSI_TAKE if kind == SikkeKind.KURSI_TAKE else KURSI_DEFENCE
    return SikkeEarned(
        player_id=player_id,
        key=f"kursi:{g.game_id}",
        kind=kind,
        amount=amount,
        earned_at=g.ended_at,
        game=ProgressionGame.KACHUFUL,
        game_id=g.game_id,
    )


def _kursi_step(k: Kursi | None, g: KursiGame) -> tuple[Kursi | None, list[SikkeEarned]]:
    after, event = step_kursi(k, g)
    if event.kind in ("first", "taken"):
        return after, [_kursi_entry(p, g, SikkeKind.KURSI_TAKE) for p in event.to]
    if event.kind == "defended" and k is not None:
        seated = [s.player_id for s in g.seats if s.player_id in k.holders]
        return after, [_kursi_entry(p, g, SikkeKind.KURSI_DEFENCE) for p in seated]
    return after, []


def kursi_entries(games: list[KursiGame]) -> list[SikkeEarned]:
    """Every take and every defence along Kachuful's Kursi chain, oldest game first."""
    kursi: Kursi | None = None
    out: list[SikkeEarned] = []
    for g in sorted(games, key=lambda x: (x.ended_at, x.game_id)):
        kursi, entries = _kursi_step(kursi, g)
        out += entries
    return out


@dataclass(frozen=True)
class TajReign:
    """Who held `medal1` from `since` until `until` (None: still holds it)."""

    holders: tuple[str, ...]
    since: int
    until: int | None


def _taj_at(reigns: list[TajReign], at: int) -> set[str]:
    return {
        h for r in reigns if r.since <= at and (r.until is None or at < r.until) for h in r.holders
    }


def _bounties(g: KursiGame, holders: set[str], week: str) -> list[SikkeEarned]:
    """Everyone strictly above a seated holder earns that holder's bounty for the week."""
    out = []
    for holder in sorted(holders):
        place = next((s.place for s in g.seats if s.player_id == holder), None)
        if place is None:
            continue
        for s in g.seats:
            if s.player_id != holder and s.place < place:
                out.append(
                    SikkeEarned(
                        player_id=s.player_id,
                        key=f"bounty:{holder}:{week}",
                        kind=SikkeKind.BOUNTY,
                        amount=BOUNTY,
                        earned_at=g.ended_at,
                        game=ProgressionGame.KACHUFUL,
                        game_id=g.game_id,
                        data={"holder": holder},
                    )
                )
    return out


def bounty_entries(
    games: list[KursiGame], created_at: dict[str, int], taj: list[TajReign], tz: ZoneInfo
) -> list[SikkeEarned]:
    """Bounties along Kachuful's chain: the holders are the Kursi's before each game and the Taj's
    at its start; the first qualifying game of an ISO week (APP_TZ) pays each key."""
    kursi: Kursi | None = None
    out: dict[tuple[str, str], SikkeEarned] = {}
    for g in sorted(games, key=lambda x: (x.ended_at, x.game_id)):
        if len(g.seats) >= BOUNTY_MIN_TABLE:
            holders = set(kursi.holders if kursi else ()) | _taj_at(taj, created_at[g.game_id])
            week = _week_label(day_of(g.ended_at, tz))
            for e in _bounties(g, holders, week):
                out.setdefault((e.player_id, e.key), e)
        kursi, _ = step_kursi(kursi, g)
    return list(out.values())


def sitara_entries(night_id: str, ended_at: int, holders: list[str]) -> list[SikkeEarned]:
    """A closed night's Sitara pay: each of its Sitara, dated at the night's end."""
    return [
        SikkeEarned(
            player_id=pid,
            key=f"sitara:{night_id}",
            kind=SikkeKind.SITARA,
            amount=SITARA,
            earned_at=ended_at,
            game=ProgressionGame.KACHUFUL,
            data={"nightId": night_id},
        )
        for pid in holders
    ]
