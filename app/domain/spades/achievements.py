"""Spades achievements (plan §3; app: src/games/spades/domain/achievements.ts).

One catalogue for both kinds (`spades` and `spades-online`). Hand moments go to whoever played the
hand (`hand.players` online, after a Replace), game codes to whoever sat at the end, as the boards
count them. The online-only codes read a hand's 13 tricks. A cancelled game earns nothing.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.progression import (
    CREW_STEPS,
    Earned,
    PlayedGame,
    is_night,
    shared_career,
    shared_ladders,
)
from app.domain.spades.seats import SEATS, other_team, seats_of, team_of
from app.domain.spades.standings import hand_lines, totals

GAMES_STEPS = (10, 50, 100, 250)
HANDS_STEPS = (100, 500, 1000)
MADE_STEPS = (50, 250, 500)
NILS_STEPS = (5, 25, 50)
COMEBACK_GAP = 150
PAIR_WINS = 10
_ONLINE_TRICKS = 13

# Hand moments and game-end codes: the rows one stored game owns.
MOMENT_CODES: frozenset[str] = frozenset(
    {
        "boston",
        "ice_cold",
        "blindside",
        "double_nil",
        "nil_breaker",
        "set_them",
        "high_roller",
        "bang_on",
        "sandbagged",
        "blind_spot",
        "last_gasp",
        "deuce_ace",
        "one_man_band",
    }
)
GAME_CODES: frozenset[str] = frozenset(
    {
        "winner",
        "comeback",
        "exact",
        "mercy",
        "shutout",
        "wire_to_wire",
        "photo_finish",
        "runaway",
        "bag_collector",
        "marathon",
        "night_owl",
    }
)
# Career codes: recomputed per player over every finished game.
CAREER_CODES: frozenset[str] = frozenset(
    {"first_win", "back_to_back", "hat_trick", "dynasty", "perfect_pair"}
    | {f"games_{n}" for n in GAMES_STEPS}
    | {f"hands_{n}" for n in HANDS_STEPS}
    | {f"made_{n}" for n in MADE_STEPS}
    | {f"nils_{n}" for n in NILS_STEPS}
    | {f"crew_{n}" for n in CREW_STEPS}
)
# The ladders `ladder_counts` returns (ids match the app's), in the app's order.
LADDER_IDS: tuple[str, ...] = ("wins", "games", "hands", "made", "nils", "crew")


@dataclass(frozen=True)
class SpGameFacts:
    """One stored game of either kind, as hand and game achievements read it."""

    game_id: str
    status: str
    result: dict[str, Any] | None
    seats: tuple[str | None, ...]
    finished_at: int | None
    updated_at: int
    hands: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SpRow:
    """One `spades_game_players` row: `result` is None for a seat replaced before the end."""

    game_id: str
    player_id: str
    ended_at: int
    result: str | None
    partner_id: str | None
    hands: int
    bids: int
    bids_made: int
    nils_made: int
    bags: int


@dataclass(frozen=True)
class SpCareerFacts:
    """Some players' finished games: their rows, and who sat at the end of each of those games."""

    player_ids: tuple[str, ...]
    rows: tuple[SpRow, ...] = ()
    tables: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _seats_of_payload(kind: str, payload: dict[str, Any]) -> tuple[str | None, ...]:
    if kind == "spades-online":
        return tuple(
            s["who"].get("playerId") if isinstance(s.get("who"), dict) else None
            for s in payload["seats"]
        )
    return tuple(p.get("playerId") for p in payload["players"])


def game_facts_of(kind: str, game_id: str, payload: dict[str, Any]) -> SpGameFacts:
    """A stored payload of either kind (`spades` or `spades-online`) as the rules read it."""
    hands = payload.get("history" if kind == "spades-online" else "hands") or []
    status = str(payload.get("status"))
    finished = payload.get("finishedAt")
    return SpGameFacts(
        game_id=game_id,
        status=status if status in ("finished", "cancelled") else "active",
        result=payload.get("result") or None,
        seats=_seats_of_payload(kind, payload),
        finished_at=None if finished is None else int(finished),
        updated_at=int(payload["updatedAt"]),
        hands=tuple(h for h in hands if isinstance(h, dict)),
    )


def _hand_players(hand: dict[str, Any], seats: Sequence[str | None]) -> Sequence[str | None]:
    played = hand.get("players")
    return played if isinstance(played, list) and len(played) == 4 else seats


def _last_gasp(hand: dict[str, Any], team: str, tricks: Sequence[dict[str, Any]]) -> bool:
    """The team's bidders reach their contract with the 13th trick, not before."""
    bidders = [s for s in seats_of(team) if hand["bids"][s] != 0]
    if not bidders:
        return False
    contract = sum(hand["bids"][s] or 0 for s in bidders)
    before = sum(1 for x in tricks[:12] if x["winner"] in bidders)
    return before == contract - 1 and tricks[12]["winner"] in bidders


def _is_card(card: dict[str, Any], rank: str, suit: str) -> bool:
    return bool(card["rank"] == rank and card["suit"] == suit)


def _deuce_ace(tricks: Sequence[dict[str, Any]], seat: int) -> bool:
    """`seat` won a trick led in another suit with the 2♠ while that suit's Ace was in it."""
    for x in tricks:
        plays = x["plays"]
        led = plays[0]["card"]["suit"] if plays else None
        if led == "spades" or x["winner"] != seat:
            continue
        deuce = any(p["seat"] == seat and _is_card(p["card"], "2", "spades") for p in plays)
        if deuce and any(_is_card(p["card"], "A", str(led)) for p in plays):
            return True
    return False


def _online_codes(hand: dict[str, Any], seat: int, tricks: Sequence[dict[str, Any]]) -> list[str]:
    wins = sum(1 for x in tricks if x["winner"] == seat)
    checks = (
        (_last_gasp(hand, team_of(seat), tricks), "last_gasp"),
        (_deuce_ace(tricks, seat), "deuce_ace"),
        (wins >= 7, "one_man_band"),
    )
    return [code for ok, code in checks if ok]


def _hand_codes(hand: dict[str, Any], line: dict[str, Any], seat: int) -> list[str]:
    """The codes one seat earns in one scored hand, in catalogue order."""
    team = team_of(seat)
    mine, theirs = line[team], line[other_team(team)]
    nil = next((n for n in mine["nils"] if n["seat"] == seat), None)
    team_tricks = hand.get("teamTricks") or [0, 0]
    checks = (
        (team_tricks[0 if team == "a" else 1] == 13, "boston"),
        (nil is not None and nil["made"] and not nil["blind"], "ice_cold"),
        (nil is not None and nil["made"] and nil["blind"], "blindside"),
        (len(mine["nils"]) == 2 and all(n["made"] for n in mine["nils"]), "double_nil"),
        (any(not n["made"] for n in theirs["nils"]), "nil_breaker"),
        (theirs["made"] is False, "set_them"),
        (mine["made"] is True and mine["contract"] >= 10, "high_roller"),
        (mine["made"] is True and mine["contract"] >= 7 and mine["bags"] == 0, "bang_on"),
        (mine["penalty"] > 0, "sandbagged"),
        (nil is not None and nil["blind"] and not nil["made"], "blind_spot"),
    )
    out = [code for ok, code in checks if ok]
    tricks = hand.get("tricks")
    if isinstance(tricks, list) and len(tricks) == _ONLINE_TRICKS:
        out += _online_codes(hand, seat, tricks)
    return out


def _hand_moments(facts: SpGameFacts, hand: dict[str, Any], line: dict[str, Any]) -> list[Earned]:
    players = _hand_players(hand, facts.seats)
    done_at = hand.get("doneAt")
    at = done_at if isinstance(done_at, int) else facts.updated_at
    out: list[Earned] = []
    for seat in SEATS:
        pid = players[seat]
        if pid:
            out += [
                Earned(code, str(pid), facts.game_id, int(hand["index"]), None, at, {})
                for code in _hand_codes(hand, line, seat)
            ]
    return out


def _won_codes(result: dict[str, Any], lines: list[dict[str, Any]], team: str) -> list[str]:
    other = other_team(team)
    last = lines[-1]["after"] if lines else {"a": 0, "b": 0}
    margin = last[team] - last[other]
    exact = all(x[team]["made"] is not False and x[team]["bags"] == 0 for x in lines)
    checks = (
        (True, "winner"),
        (any(x["after"][other] - x["after"][team] >= COMEBACK_GAP for x in lines), "comeback"),
        (len(lines) >= 4 and exact, "exact"),
        (result.get("endedBy") == "mercy", "mercy"),
        (len(lines) >= 3 and all(x["after"][other] <= 0 for x in lines), "shutout"),
        (
            len(lines) >= 5 and all(x["after"][team] > x["after"][other] for x in lines),
            "wire_to_wire",
        ),
        (margin <= 20, "photo_finish"),
        (margin >= 200, "runaway"),
    )
    return [code for ok, code in checks if ok]


def _ended_at(facts: SpGameFacts) -> int:
    return facts.finished_at if facts.finished_at is not None else facts.updated_at


def _end_codes(
    facts: SpGameFacts, result: dict[str, Any], lines: list[dict[str, Any]], seat: int, tz: ZoneInfo
) -> list[str]:
    """The codes one seat at the end earns when the game finishes, in catalogue order."""
    team = team_of(seat)
    out = _won_codes(result, lines, team) if result.get("winner") == team else []
    checks = (
        (totals(facts.hands)[team]["penalties"] >= 2, "bag_collector"),
        (len(lines) >= 15, "marathon"),
        (is_night(_ended_at(facts), tz), "night_owl"),
    )
    return out + [code for ok, code in checks if ok]


def game_achievements(facts: SpGameFacts, tz: ZoneInfo) -> list[Earned]:
    """Hand moments and game-end achievements of one game; "night" is judged in `tz`."""
    if facts.status == "cancelled":
        return []
    lines = hand_lines(facts.hands)
    by_index = {h.get("index"): h for h in facts.hands}
    out = [e for line in lines for e in _hand_moments(facts, by_index[line["index"]], line)]
    result = facts.result
    if facts.status != "finished" or not result:
        return out
    for seat, pid in enumerate(facts.seats[:4]):
        if pid:
            out += [
                Earned(code, pid, facts.game_id, None, None, _ended_at(facts), {})
                for code in _end_codes(facts, result, lines, seat, tz)
            ]
    return out


def _row_earned(row: SpRow, code: str, data: dict[str, Any]) -> Earned:
    return Earned(code, row.player_id, row.game_id, None, None, row.ended_at, data)


_COUNTS: tuple[tuple[str, tuple[int, ...], Callable[[SpRow], int]], ...] = (
    ("hands", HANDS_STEPS, lambda r: r.hands),
    ("made", MADE_STEPS, lambda r: r.bids_made),
    ("nils", NILS_STEPS, lambda r: r.nils_made),
)


def _count_ladders(rows: Sequence[SpRow]) -> list[Earned]:
    """hands_N, made_N and nils_N at the game that takes one player's running count to N."""
    out: list[Earned] = []
    run: dict[str, int] = defaultdict(int)
    for row in rows:
        for name, steps, count in _COUNTS:
            before = run[name]
            run[name] += count(row)
            out += [
                _row_earned(row, f"{name}_{n}", {"n": n}) for n in steps if before < n <= run[name]
            ]
    return out


def _perfect_pair(rows: Sequence[SpRow]) -> list[Earned]:
    """One player's Perfect Pairs: once per partner, at the tenth win together."""
    out: list[Earned] = []
    wins: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.result != "win" or row.partner_id is None:
            continue
        wins[row.partner_id] += 1
        if wins[row.partner_id] == PAIR_WINS:
            out.append(_row_earned(row, "perfect_pair", {"partner": row.partner_id}))
    return out


def _played(player_id: str, rows: Sequence[SpRow], facts: SpCareerFacts) -> list[PlayedGame]:
    return [
        PlayedGame(
            player_id=player_id,
            game_id=r.game_id,
            ended_at=r.ended_at,
            won=r.result == "win",
            others=facts.tables.get(r.game_id, ()),
        )
        for r in rows
        if r.result is not None
    ]


def _mine(facts: SpCareerFacts, player_id: str) -> list[SpRow]:
    rows = [r for r in facts.rows if r.player_id == player_id]
    return sorted(rows, key=lambda r: (r.ended_at, r.game_id))


def career_achievements(facts: SpCareerFacts) -> list[Earned]:
    """Every career achievement the players in `facts` hold."""
    out: list[Earned] = []
    for pid in facts.player_ids:
        rows = _mine(facts, pid)
        out += shared_career(pid, _played(pid, rows, facts), games_steps=GAMES_STEPS)
        out += _count_ladders(rows)
        out += _perfect_pair(rows)
    return out


def ladder_counts(facts: SpCareerFacts, player_id: str) -> dict[str, int]:
    """Each ladder's count for one player, counted the way its codes are earned."""
    rows = _mine(facts, player_id)
    counts = shared_ladders(player_id, _played(player_id, rows, facts))
    counts |= {name: sum(count(r) for r in rows) for name, _, count in _COUNTS}
    return {k: counts[k] for k in LADDER_IDS}
