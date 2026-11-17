"""Ishaara achievements (app: src/games/ishaara/domain/achievements.ts): turn moments and game-end
codes from a finished game's public log, career codes and ladders from its player rows.

Guesses are the team's, never one person's, so a code credits the whole team, its guessers or its
clue-giver. Moment rows carry `round_index` = the turn (the clue's place among the game's clues).
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.domain.ishaara.types import Team, as_team, other
from app.domain.progression import Earned, PlayedGame, is_night, shared_career, shared_ladders

MOMENT_CODES: frozenset[str] = frozenset(
    {"mind_meld", "one_word_four", "overtime", "trapped", "own_goal"}
)
GAME_CODES: frozenset[str] = frozenset(
    {
        "winner",
        "flawless",
        "speedrun",
        "comeback",
        "photo_finish",
        "lucky_escape",
        "mastermind",
        "series",
        "whitewash",
        "white_flag",
        "night_owl",
    }
)
GAMES_STEPS = (10, 50, 100, 250)
GIVER_WINS_STEPS = (5, 25, 50)
CLUES_STEPS = (25, 100, 250)
FOUND_STEPS = (50, 250, 1000)
CAREER_CODES: frozenset[str] = frozenset(
    {"first_win", "back_to_back", "hat_trick", "dynasty", "crew_10", "crew_25"}
    | {f"games_{n}" for n in GAMES_STEPS}
    | {f"giver_wins_{n}" for n in GIVER_WINS_STEPS}
    | {f"clues_{n}" for n in CLUES_STEPS}
    | {f"found_{n}" for n in FOUND_STEPS}
)
# The ladders `ladder_counts` returns (ids match the app's), in the app's order.
LADDER_IDS: tuple[str, ...] = ("wins", "games", "giver_wins", "clues", "found", "crew")

SPEEDRUN_MAX_CLUES = 4
COMEBACK_BEHIND_CARDS = 4
MASTERMIND_MIN_CLUES = 3
SERIES_WINS = 2
# The starting team has one card more to find.
STARTING_CARDS = 9
OTHER_CARDS = 8

Credit = Literal["team", "guessers", "giver"]
CLUE_GIVER = "clue-giver"
GUESSER = "guesser"


@dataclass(frozen=True)
class IshSeat:
    """A registered seat: who, on which team, in which role."""

    player_id: str
    team: Team
    role: str


@dataclass(frozen=True)
class IshLogEntry:
    """One public log entry, the fields the rules read (`number`: 0-9, "inf", or None)."""

    type: str
    team: str
    at: int
    number: int | str | None = None
    identity: str | None = None


@dataclass(frozen=True)
class IshGameFacts:
    """One stored game of finished (not abandoned) Ishaara, as turn and game achievements read it."""

    game_id: str
    players: tuple[IshSeat, ...]
    starting: Team
    # The public log: an undone guess has already left it.
    log: tuple[IshLogEntry, ...]
    winner: Team
    ended_by: str
    finished_at: int
    best_of: int
    # Both teams' match wins after this game, this game's included.
    sun_wins: int
    moon_wins: int


@dataclass(frozen=True)
class IshCareerRow:
    """One seat of a finished (not abandoned) game: an ishaara_game_players row."""

    game_id: str
    player_id: str
    finished_at: int
    won: bool
    role: str
    words_found: int
    clues_given: int


@dataclass(frozen=True)
class IshCareerFacts:
    """Some players' finished games, every seat of each (crew counts them), as careers read them."""

    player_ids: tuple[str, ...]
    rows: tuple[IshCareerRow, ...] = ()


@dataclass(frozen=True)
class _Turn:
    index: int
    team: str
    number: int | str
    guesses: tuple[IshLogEntry, ...]


@dataclass(frozen=True)
class _Moment:
    code: str
    team: str
    credit: Credit
    turn: int
    at: int


def _entry(raw: dict[str, Any]) -> IshLogEntry:
    return IshLogEntry(
        type=str(raw["type"]),
        team=str(raw["team"]),
        at=int(raw["at"]),
        number=raw.get("number"),
        identity=raw.get("identity"),
    )


def game_facts_of(state: dict[str, Any]) -> IshGameFacts | None:
    """A finished game's facts from its public state; None while played, abandoned or winnerless."""
    ended_by = state.get("endedBy")
    winner = state.get("winner")
    if state.get("status") != "finished" or not winner or not ended_by or ended_by == "abandoned":
        return None
    finished = state.get("finishedAt")
    return IshGameFacts(
        game_id=str(state["id"]),
        players=tuple(
            IshSeat(player_id=str(p["playerId"]), team=as_team(p["team"]), role=str(p["role"]))
            for p in state["players"]
            if p.get("playerId")
        ),
        starting=as_team(state["starting"]),
        log=tuple(_entry(e) for e in state["log"]),
        winner=as_team(winner),
        ended_by=str(ended_by),
        finished_at=int(state["updatedAt"] if finished is None else finished),
        best_of=int(state["rules"]["bestOf"]),
        sun_wins=int(state["match"]["sunWins"]),
        moon_wins=int(state["match"]["moonWins"]),
    )


def _turns(log: Sequence[IshLogEntry]) -> list[_Turn]:
    """Each clue and the guesses after it, until the next clue."""
    clues: list[IshLogEntry] = []
    guesses: list[list[IshLogEntry]] = []
    for e in log:
        if e.type == "clue":
            clues.append(e)
            guesses.append([])
        elif e.type == "guess" and guesses:
            guesses[-1].append(e)
    return [
        _Turn(k, c.team, 0 if c.number is None else c.number, tuple(g))
        for k, (c, g) in enumerate(zip(clues, guesses, strict=True))
    ]


def _clue_count(t: _Turn) -> int | None:
    """A clue's number when it is 1 or more (0 and "inf" are never fully found)."""
    n = t.number
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None


def _fully_found(t: _Turn) -> bool:
    n = _clue_count(t)
    return (
        n is not None and len(t.guesses) >= n and all(g.identity == t.team for g in t.guesses[:n])
    )


def _found_moments(t: _Turn) -> list[_Moment]:
    n = _clue_count(t)
    if n is None or not _fully_found(t):
        return []
    out: list[_Moment] = []
    if n == 3:
        out.append(_Moment("mind_meld", t.team, "team", t.index, t.guesses[2].at))
    if n >= 4:
        out.append(_Moment("one_word_four", t.team, "team", t.index, t.guesses[n - 1].at))
    if len(t.guesses) > n and t.guesses[n].identity == t.team:
        out.append(_Moment("overtime", t.team, "team", t.index, t.guesses[n].at))
    return out


def _turn_moments(f: IshGameFacts) -> list[_Moment]:
    out: list[_Moment] = []
    last = f.log[-1] if f.log else None
    for t in _turns(f.log):
        out += _found_moments(t)
        for g in t.guesses:
            if g.identity == "trap":
                out.append(_Moment("trapped", t.team, "guessers", t.index, g.at))
            # The guess that ended the game on the other team's last card.
            own_goal = g.identity == other(t.team) and f.winner == g.identity
            if g is last and f.ended_by == "all-found" and own_goal:
                out.append(_Moment("own_goal", t.team, "guessers", t.index, g.at))
    return out


def _credited(f: IshGameFacts, team: str, credit: Credit) -> list[IshSeat]:
    role = {"guessers": GUESSER, "giver": CLUE_GIVER}.get(credit)
    return [p for p in f.players if p.team == team and (role is None or p.role == role)]


def _left_after_each(f: IshGameFacts) -> list[dict[str, int]]:
    """Each team's face-down cards after every reveal (a guess or a penalty), from the start."""
    left: dict[str, int] = {f.starting: STARTING_CARDS, other(f.starting): OTHER_CARDS}
    out = [dict(left)]
    for e in f.log:
        if e.type in ("guess", "penalty") and e.identity in ("sun", "moon"):
            left[str(e.identity)] -= 1
            out.append(dict(left))
    return out


def _sort_key(e: Earned) -> tuple[int, str, str, int]:
    """Oldest first, then code, player and turn: the app sorts the same way."""
    return (e.earned_at, e.code, e.player_id, -1 if e.round_index is None else e.round_index)


def _end_codes(f: IshGameFacts) -> list[tuple[str, str, Credit]]:
    """The game-end codes earned: (code, team, credit)."""
    win, lose = f.winner, other(f.winner)
    turns = [t for t in _turns(f.log) if t.team == win]
    wrong = any(e.type == "guess" and e.team == win and e.identity != win for e in f.log)
    left = _left_after_each(f)
    wins = {"sun": f.sun_wins, "moon": f.moon_wins}
    series = f.best_of == 3 and wins[win] == SERIES_WINS
    mastermind = len(turns) >= MASTERMIND_MIN_CLUES and all(_fully_found(t) for t in turns)
    rules: list[tuple[bool, str, str, Credit]] = [
        (True, "winner", win, "team"),
        (f.ended_by != "resign" and not wrong, "flawless", win, "team"),
        (f.ended_by == "all-found" and len(turns) <= SPEEDRUN_MAX_CLUES, "speedrun", win, "team"),
        (any(x[win] - x[lose] >= COMEBACK_BEHIND_CARDS for x in left), "comeback", win, "team"),
        (left[-1][lose] == 1, "photo_finish", win, "team"),
        (f.ended_by == "trap", "lucky_escape", win, "team"),
        (mastermind, "mastermind", win, "giver"),
        (series, "series", win, "team"),
        (series and wins[lose] == 0, "whitewash", win, "team"),
        (f.ended_by == "resign", "white_flag", lose, "team"),
    ]
    return [(code, team, credit) for ok, code, team, credit in rules if ok]


def game_achievements(facts: IshGameFacts, tz: ZoneInfo) -> list[Earned]:
    """Turn moments and game-end achievements of one game; "night" is judged in `tz`."""
    f = facts
    out = [
        Earned(m.code, p.player_id, f.game_id, m.turn, None, m.at, {})
        for m in _turn_moments(f)
        for p in _credited(f, m.team, m.credit)
    ]
    out += [
        Earned(code, p.player_id, f.game_id, None, None, f.finished_at, {})
        for code, team, credit in _end_codes(f)
        for p in _credited(f, team, credit)
    ]
    if is_night(f.finished_at, tz):
        out += [
            Earned("night_owl", p.player_id, f.game_id, None, None, f.finished_at, {})
            for p in f.players
        ]
    return sorted(out, key=_sort_key)


# Careers.


def _giver_win(r: IshCareerRow) -> int:
    return 1 if r.won and r.role == CLUE_GIVER else 0


def _clues(r: IshCareerRow) -> int:
    return r.clues_given


def _found(r: IshCareerRow) -> int:
    return r.words_found


# Each running-total ladder: its code prefix, steps and what one game adds.
_TOTALS: tuple[tuple[str, tuple[int, ...], Callable[[IshCareerRow], int]], ...] = (
    ("giver_wins", GIVER_WINS_STEPS, _giver_win),
    ("clues", CLUES_STEPS, _clues),
    ("found", FOUND_STEPS, _found),
)


def _ended(rows: Iterable[IshCareerRow]) -> list[IshCareerRow]:
    return sorted(rows, key=lambda r: (r.finished_at, r.game_id))


def _tables(rows: Iterable[IshCareerRow]) -> dict[str, list[str]]:
    """Everyone seated in each game."""
    tables: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        tables[r.game_id].append(r.player_id)
    return tables


def _played(mine: Sequence[IshCareerRow], tables: dict[str, list[str]]) -> list[PlayedGame]:
    return [
        PlayedGame(r.player_id, r.game_id, r.finished_at, r.won, tuple(tables[r.game_id]))
        for r in mine
    ]


def _totals(mine: Sequence[IshCareerRow]) -> list[Earned]:
    """`<prefix>_N` at the game whose running total goes from below N to N or more."""
    out: list[Earned] = []
    for prefix, steps, count in _TOTALS:
        total = 0
        for r in mine:
            before, total = total, total + count(r)
            out += [
                Earned(f"{prefix}_{n}", r.player_id, r.game_id, None, None, r.finished_at, {"n": n})
                for n in steps
                if before < n <= total
            ]
    return out


def career_achievements(facts: IshCareerFacts) -> list[Earned]:
    """Every career achievement the players in `facts.player_ids` hold (crew-mates' rows aside)."""
    rows = _ended(facts.rows)
    tables = _tables(rows)
    out: list[Earned] = []
    for player_id in facts.player_ids:
        mine = [r for r in rows if r.player_id == player_id]
        out += shared_career(player_id, _played(mine, tables), games_steps=GAMES_STEPS)
        out += _totals(mine)
    return sorted(out, key=_sort_key)


def ladder_counts(facts: IshCareerFacts, player_id: str) -> dict[str, int]:
    """Each ladder's count for one player, counted the way its codes are earned."""
    rows = _ended(facts.rows)
    mine = [r for r in rows if r.player_id == player_id]
    counts = shared_ladders(player_id, _played(mine, _tables(rows)))
    counts |= {prefix: sum(count(r) for r in mine) for prefix, _, count in _TOTALS}
    return {ladder: counts[ladder] for ladder in LADDER_IDS}
