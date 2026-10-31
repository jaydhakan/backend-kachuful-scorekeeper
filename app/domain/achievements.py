"""Achievements (plan §8.1-8.3; app: src/domain/achievements.ts). Ties earn together."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.derive import Facts, GameFact, RoundFact
from app.domain.time import hour_of

STEPS = (3, 5, 8, 12)
CAREER_GAMES = (10, 50, 100, 250, 500)
CAREER_ROUNDS = (100, 500, 1000, 5000)
CAREER_MADE = (100, 500, 1000)
CAREER_POINTS = (1000, 5000, 10000)


@dataclass(frozen=True)
class AchievementDef:
    """A catalogue entry; the app renders names from `code`."""

    code: str
    emoji: str
    name: str
    group: str


def _defs(group: str, rows: Iterable[tuple[str, str, str]]) -> list[AchievementDef]:
    return [AchievementDef(code, emoji, name, group) for code, emoji, name in rows]


CATALOGUE: dict[str, AchievementDef] = {
    d.code: d
    for d in [
        *_defs(
            "round",
            [
                ("hot_3", "🔥", "On fire"),
                ("hot_5", "🚀", "Unstoppable"),
                ("hot_8", "🐐", "G.O.A.T."),
                ("hot_12", "💎", "Untouchable"),
                ("cold_3", "🥶", "Ice cold"),
                ("cold_5", "💀", "Cursed"),
                ("cold_8", "🪦", "Rest in peace"),
                ("cold_12", "🧟", "Zombie"),
                ("grand_slam", "💯", "Grand slam"),
                ("ninja", "🥷", "Ninja"),
                ("high_roller", "🎰", "High roller"),
                ("peak_performer", "🏔️", "Peak performer"),
                ("lone_wolf", "🐺", "Lone wolf"),
                ("odd_one_out", "🫠", "Odd one out"),
                ("hooked", "🪝", "Hooked"),
                ("dealers_revenge", "😈", "Dealer's revenge"),
            ],
        ),
        *_defs(
            "game",
            [
                ("winner", "🏆", "Winner"),
                ("wire_to_wire", "🏁", "Wire to wire"),
                ("comeback", "🔄", "Comeback king"),
                ("photo_finish", "📸", "Photo finish"),
                ("runaway", "🏃", "Runaway"),
                ("sharpshooter", "🎯", "Sharpshooter"),
                ("perfect_game", "🌟", "Perfect game"),
                ("wooden_spoon", "🥄", "Wooden spoon"),
                ("zero_hero", "0️⃣", "Zero hero"),
                ("marathon", "🏃‍♂️", "Marathon"),
                ("full_house", "🏠", "Full house"),
                ("night_owl", "🦉", "Night owl"),
            ],
        ),
        *_defs(
            "career",
            [
                ("first_win", "🥇", "First win"),
                ("back_to_back", "✌️", "Back to back"),
                ("hat_trick", "🎩", "Hat-trick"),
                ("dynasty", "🦸", "Dynasty"),
                ("spoon_collector", "🥄", "Spoon collector"),
                ("podium_regular", "🥉", "Podium regular"),
                ("personal_best", "📈", "Personal best"),
                *[(f"games_{n}", "🎲", f"{n} games") for n in CAREER_GAMES],
                *[(f"rounds_{n}", "🔁", f"{n} rounds") for n in CAREER_ROUNDS],
                *[(f"made_{n}", "✅", f"{n} bids made") for n in CAREER_MADE],
                *[(f"points_{n}", "💰", f"{n} points earned") for n in CAREER_POINTS],
                ("crew_10", "👥", "Crew of 10"),
                ("crew_25", "👥", "Crew of 25"),
                ("rivalry_20", "⚔️", "Rivalry"),
            ],
        ),
        *_defs(
            "daily",
            [
                ("day_star", "⭐", "Player of the day"),
                ("day_bang", "💥", "Round of the day"),
                ("day_ouch", "🤕", "Ouch of the day"),
                ("day_hot", "🔥", "Hot hand"),
                ("day_ice", "🧊", "Ice bucket"),
                ("day_sharp", "🎯", "Sharpest"),
                ("day_grinder", "⏱️", "Grinder"),
                ("day_hooked", "🪝", "Most hooked"),
            ],
        ),
        *_defs(
            "season",
            [
                ("season_champion", "👑", "Season champion"),
                ("season_runner_up", "🥈", "Season runner-up"),
                ("season_third", "🥉", "Season third"),
                ("season_sharpest", "🎯", "Season sharpest"),
                ("season_iron", "🦾", "Iron player"),
                ("season_climber", "📈", "Climber"),
            ],
        ),
    ]
}

GAME_SCOPED_CODES = frozenset(c for c, d in CATALOGUE.items() if d.group in ("round", "game"))
CAREER_CODES = frozenset(c for c, d in CATALOGUE.items() if d.group == "career")
ROUND_MILESTONE_CODES = frozenset(
    c for c in CAREER_CODES if c.startswith(("rounds_", "made_", "points_"))
)


@dataclass(frozen=True)
class Earned:
    """One earned achievement (the contract's Achievement without the row id)."""

    code: str
    player_id: str
    game_id: str | None
    round_index: int | None
    period: str | None
    earned_at: int
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str | None, int | None, str | None]:
        """Earned at most once per player, code, game, round and period."""
        return (self.player_id, self.code, self.game_id, self.round_index, self.period)


def _at(r: RoundFact, code: str, data: dict[str, Any] | None = None) -> Earned:
    return Earned(code, r.player_id, r.game_id, r.round_index, None, r.done_at, data or {})


def _fin(p: GameFact, code: str, data: dict[str, Any] | None = None) -> Earned:
    assert p.ended_at is not None
    return Earned(code, p.player_id, p.game_id, None, None, p.ended_at, data or {})


def _round_table_moments(table: list[RoundFact]) -> list[Earned]:
    out: list[Earned] = []
    missed = sum(1 for r in table if r.outcome == "missed")
    made_rows = sum(1 for r in table if r.outcome == "made")
    for r in table:
        made = r.outcome == "made"
        if made and r.run_hot in STEPS:
            out.append(_at(r, f"hot_{r.run_hot}", {"n": r.run_hot}))
        if not made and r.run_cold in STEPS:
            out.append(_at(r, f"cold_{r.run_cold}", {"n": r.run_cold}))
        if made and r.bid == r.cards and r.cards >= 5:
            out.append(_at(r, "grand_slam", {"cards": r.cards}))
        if made and r.bid == 0 and r.cards >= 7:
            out.append(_at(r, "ninja", {"cards": r.cards}))
        if made and r.bid >= 5:
            out.append(_at(r, "high_roller", {"bid": r.bid}))
        if made and r.bid >= 3 and r.cards == r.max_cards:
            out.append(_at(r, "peak_performer", {"bid": r.bid}))
        if r.table_size >= 4 and made and made_rows == 1:
            out.append(_at(r, "lone_wolf"))
        if r.table_size >= 4 and not made and missed == 1:
            out.append(_at(r, "odd_one_out"))
        if r.is_dealer and r.hook_hits >= 1:
            out.append(_at(r, "hooked", {"n": r.hook_hits}))
            if made:
                out.append(_at(r, "dealers_revenge"))
    return out


def round_moments(rounds: Iterable[RoundFact]) -> list[Earned]:
    """§8.1, earned at each round's doneAt."""
    tables: dict[tuple[str, int], list[RoundFact]] = defaultdict(list)
    for r in rounds:
        tables[(r.game_id, r.round_index)].append(r)
    return [e for table in tables.values() for e in _round_table_moments(table)]


def _places_after_each_round(rounds: list[RoundFact]) -> list[dict[str, int]]:
    totals: dict[str, int] = {}
    out: list[dict[str, int]] = []
    for index in sorted({r.round_index for r in rounds}):
        for r in rounds:
            if r.round_index == index:
                totals[r.player_id] = totals.get(r.player_id, 0) + r.points
        values = list(totals.values())
        out.append({pid: 1 + sum(1 for v in values if v > t) for pid, t in totals.items()})
    return out


def _finish_moments(
    ps: list[GameFact], steps: list[dict[str, int]], n: int, table_size: int, hour: int
) -> list[Earned]:
    out: list[Earned] = []
    worst = max(p.place or 0 for p in ps)
    ranked = sorted(ps, key=lambda p: -p.points)
    winners = sum(1 for p in ps if p.place == 1)
    for p in ps:
        won = p.place == 1
        sole = won and winners == 1
        margin = p.points - ranked[1].points if sole else 0
        if won:
            out.append(_fin(p, "winner", {"points": p.points}))
        if won and n >= 6 and all(s.get(p.player_id) == 1 for s in steps):
            out.append(_fin(p, "wire_to_wire"))
        if (
            won
            and len(ps) > 1
            and any(
                (k + 1) * 2 >= n and k < len(steps) - 1 and s.get(p.player_id) == max(s.values())
                for k, s in enumerate(steps)
            )
        ):
            out.append(_fin(p, "comeback"))
        if sole and margin <= 10:
            out.append(_fin(p, "photo_finish", {"by": margin}))
        if sole and margin >= 100:
            out.append(_fin(p, "runaway", {"by": margin}))
        if p.rounds >= 10 and p.made / p.rounds >= 0.8:
            out.append(_fin(p, "sharpshooter", {"made": p.made, "rounds": p.rounds}))
        if p.rounds >= 6 and p.made == p.rounds:
            out.append(_fin(p, "perfect_game"))
        if worst > 1 and p.place == worst:
            out.append(_fin(p, "wooden_spoon"))
        if p.rounds >= 6 and p.points == 0:
            out.append(_fin(p, "zero_hero"))
        if n >= 30:
            out.append(_fin(p, "marathon", {"rounds": n}))
        if table_size == 10:
            out.append(_fin(p, "full_house"))
        if 1 <= hour < 5:
            out.append(_fin(p, "night_owl"))
    return out


def game_finishes(facts: Facts, tz: ZoneInfo) -> list[Earned]:
    """§8.2 for every ended game, earned at its end; "night" is judged in `tz`."""
    out: list[Earned] = []
    rounds_by: dict[str, list[RoundFact]] = defaultdict(list)
    for r in facts.rounds:
        rounds_by[r.game_id].append(r)
    for info in facts.games:
        if info.ended_at is None or not info.rounds:
            continue
        ps = [p for p in facts.players if p.game_id == info.game_id and p.place is not None]
        if not ps:
            continue
        steps = _places_after_each_round(rounds_by[info.game_id])
        out += _finish_moments(ps, steps, info.rounds, info.table_size, hour_of(info.ended_at, tz))
    return out


@dataclass(frozen=True)
class CareerGame:
    """One of a player's ended games, with the table's worst place and who sat there."""

    player_id: str
    game_id: str
    points: int
    place: int
    won: bool
    ended_at: int
    worst_place: int
    others: tuple[str, ...]


@dataclass(frozen=True)
class MilestoneRow:
    """A player's round with running counts (rounds, made, positive points) up to and including it."""

    player_id: str
    game_id: str
    round_index: int
    done_at: int
    outcome: str
    points: int
    rounds_so_far: int
    made_so_far: int
    positive_so_far: int


def _career(g: CareerGame, code: str, data: dict[str, Any] | None = None) -> Earned:
    return Earned(code, g.player_id, g.game_id, None, None, g.ended_at, data or {})


def career_from_games(player_id: str, games: list[CareerGame]) -> list[Earned]:
    """Game-sequence career achievements for one player; `games` in (ended_at, game_id) order."""
    out: list[Earned] = []
    wins = win_run = spoon_run = podium_run = 0
    best = float("-inf")
    crew: set[str] = set()
    rivals: dict[str, int] = defaultdict(int)
    for k, g in enumerate(games):
        if g.won:
            wins += 1
            win_run += 1
            if wins == 1:
                out.append(_career(g, "first_win"))
            for run, code in ((2, "back_to_back"), (3, "hat_trick"), (5, "dynasty")):
                if win_run == run:
                    out.append(_career(g, code))
        else:
            win_run = 0
        spoon_run = spoon_run + 1 if g.worst_place > 1 and g.place == g.worst_place else 0
        if spoon_run == 3:
            out.append(_career(g, "spoon_collector"))
        podium_run = podium_run + 1 if g.place <= 3 else 0
        if podium_run == 5:
            out.append(_career(g, "podium_regular"))
        if k >= 3 and g.points > best:
            out.append(_career(g, "personal_best", {"points": g.points}))
        best = max(best, g.points)
        if k + 1 in CAREER_GAMES:
            out.append(_career(g, f"games_{k + 1}", {"n": k + 1}))
        before = len(crew)
        rival: str | None = None
        for other in sorted(g.others):
            if other == player_id:
                continue
            crew.add(other)
            rivals[other] += 1
            if rivals[other] == 20 and rival is None:
                rival = other
        for n in (10, 25):
            if before < n <= len(crew):
                out.append(_career(g, f"crew_{n}", {"n": n}))
        if rival is not None:
            out.append(_career(g, "rivalry_20", {"with": rival}))
    return out


def _milestone(r: MilestoneRow, code: str, n: int) -> Earned:
    return Earned(code, r.player_id, r.game_id, r.round_index, None, r.done_at, {"n": n})


def round_milestones(rows: Iterable[MilestoneRow]) -> list[Earned]:
    """rounds_/made_/points_ milestones from rounds carrying running counts."""
    out: list[Earned] = []
    for r in rows:
        if r.rounds_so_far in CAREER_ROUNDS:
            out.append(_milestone(r, f"rounds_{r.rounds_so_far}", r.rounds_so_far))
        if r.outcome == "made" and r.made_so_far in CAREER_MADE:
            out.append(_milestone(r, f"made_{r.made_so_far}", r.made_so_far))
        if r.points > 0:
            was = r.positive_so_far - r.points
            out += [
                _milestone(r, f"points_{n}", n)
                for n in CAREER_POINTS
                if was < n <= r.positive_so_far
            ]
    return out


def milestones_reached(rounds: int, made: int, positive: int) -> set[str]:
    """Which round milestone codes a player's totals have passed."""
    return (
        {f"rounds_{n}" for n in CAREER_ROUNDS if rounds >= n}
        | {f"made_{n}" for n in CAREER_MADE if made >= n}
        | {f"points_{n}" for n in CAREER_POINTS if positive >= n}
    )


def milestone_rows(rounds: Iterable[RoundFact]) -> list[MilestoneRow]:
    """Running counts over one player's rounds in (doneAt, gameId, roundIndex) order."""
    out: list[MilestoneRow] = []
    made = positive = 0
    for k, r in enumerate(rounds):
        made += r.outcome == "made"
        positive += max(r.points, 0)
        out.append(
            MilestoneRow(
                r.player_id,
                r.game_id,
                r.round_index,
                r.done_at,
                r.outcome,
                r.points,
                k + 1,
                made,
                positive,
            )
        )
    return out


def career_games(facts: Facts) -> dict[str, list[CareerGame]]:
    """Each player's ended games in (ended_at, game_id) order, from in-memory facts."""
    ended = sorted(
        (p for p in facts.players if p.ended_at is not None and p.place is not None),
        key=lambda p: (p.ended_at or 0, p.game_id),
    )
    tables: dict[str, list[GameFact]] = defaultdict(list)
    for p in ended:
        tables[p.game_id].append(p)
    out: dict[str, list[CareerGame]] = defaultdict(list)
    for p in ended:
        table = tables[p.game_id]
        out[p.player_id].append(
            CareerGame(
                player_id=p.player_id,
                game_id=p.game_id,
                points=p.points,
                place=p.place or 0,
                won=p.won,
                ended_at=p.ended_at or 0,
                worst_place=max(x.place or 0 for x in table),
                others=tuple(x.player_id for x in table),
            )
        )
    return out


def career_achievements(facts: Facts) -> list[Earned]:
    """§8.3 for every player in the facts (app: careerAchievements)."""
    by_player = career_games(facts)
    out: list[Earned] = []
    for player_id in dict.fromkeys(p.player_id for p in facts.players):
        out += career_from_games(player_id, by_player.get(player_id, []))
        mine = sorted(
            (r for r in facts.rounds if r.player_id == player_id),
            key=lambda r: (r.done_at, r.game_id, r.round_index),
        )
        out += round_milestones(milestone_rows(mine))
    return out


def all_achievements(facts: Facts, tz: ZoneInfo) -> list[Earned]:
    """Round, game and career achievements, oldest first (app: allAchievements)."""
    every = round_moments(facts.rounds) + game_finishes(facts, tz) + career_achievements(facts)
    return sorted(every, key=lambda e: (e.earned_at, "|".join(str(x) for x in _app_key(e))))


def _app_key(e: Earned) -> tuple[str, ...]:
    return (
        e.player_id,
        e.code,
        e.game_id or "",
        "" if e.round_index is None else str(e.round_index),
        e.period or "",
    )
