"""Kaali Teeri achievements (app: src/games/kaali-teeri/domain/achievements.ts). Pure.

The facts are the stored rows (kt_hands, kt_game_players). Moments are earned at their hand, game
codes at the game's end; careers count ended games (wins, games, crew) and every done hand (the rest).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.kaali_teeri.cards import deck_total, decks_for
from app.domain.kaali_teeri.game import Sweep
from app.domain.progression import (
    CREW_STEPS,
    WIN_RUNS,
    Earned,
    PlayedGame,
    is_night,
    shared_career,
    shared_ladders,
)

GAMES_STEPS = (10, 50, 100, 250)
HANDS_STEPS = (100, 500, 1000)
MADE_STEPS = (10, 50, 100)
PARTNER_STEPS = (10, 50, 100)
STREAK_STEPS = (3, 5, 8)
# Made hands on the same bidding side as one person for partners_in_crime.
PARTNERS_IN_CRIME_HANDS = 10
# A failed bid this short of its target is so_close.
SO_CLOSE_POINTS = 5
# Hands a game needs before its shape counts (comeback, wire_to_wire, blanked).
SHAPE_MIN_HANDS = 6
THOUSANDAIRE_POINTS = 1000
NEVER_WRONG_MIN_BIDS = 3
KINGMAKER_MIN_HANDS = 3
FULL_HOUSE_SEATS = 10
MARATHON_HANDS = 20

# Hand moments and game-end codes: the rows one stored game owns.
MOMENT_CODES: frozenset[str] = frozenset(
    {
        "high_roller",
        "all_in",
        "clean_sweep",
        "brick_wall",
        "lone_wolf",
        "double_agent",
        "giant_killer",
        "stuck_with_it",
        "so_close",
        "overcooked",
    }
)
GAME_CODES: frozenset[str] = frozenset(
    {
        "winner",
        "comeback",
        "wire_to_wire",
        "thousandaire",
        "never_wrong",
        "kingmaker",
        "wooden_spoon",
        "blanked",
        "full_house",
        "marathon",
        "night_owl",
    }
)
# Career codes: recomputed per player over every stored game.
CAREER_CODES: frozenset[str] = frozenset(
    {code for _, code in WIN_RUNS}
    | {f"games_{n}" for n in GAMES_STEPS}
    | {f"hands_{n}" for n in HANDS_STEPS}
    | {f"made_{n}" for n in MADE_STEPS}
    | {f"partner_{n}" for n in PARTNER_STEPS}
    | {f"streak_{n}" for n in STREAK_STEPS}
    | {f"crew_{n}" for n in CREW_STEPS}
    | {"partners_in_crime"}
)
# The ladders `ladder_counts` returns (ids match the app's), in the app's order.
LADDER_IDS: tuple[str, ...] = ("wins", "games", "hands", "made", "partner", "streak", "crew")


@dataclass(frozen=True)
class KtHandFact:
    """One done hand, as kt_hands stores it; `scores` holds every seat's points (keys are seats)."""

    game_id: str
    hand_index: int
    done_at: int
    # The points in play: 250 with one deck, 500 with two.
    deck: int
    bidder_id: str
    bid: int
    forced: bool
    partner_ids: tuple[str, ...]
    slots: dict[str, int]
    team_points: int
    made: bool
    sweep: Sweep
    scores: dict[str, int]

    @property
    def side(self) -> tuple[str, ...]:
        """The bidding side: the bidder, then each partner."""
        return (self.bidder_id, *self.partner_ids)

    @property
    def defenders(self) -> list[str]:
        """Every seat not on the bidding side."""
        return [p for p in self.scores if p not in self.side]


@dataclass(frozen=True)
class KtSeatFact:
    """One seat of a stored game, as kt_game_players stores it; place and ended_at once ended."""

    game_id: str
    player_id: str
    points: int
    place: int | None
    ended_at: int | None
    hands_bid: int
    bids_made: int
    partner_made: int


@dataclass(frozen=True)
class KtGameFacts:
    """One stored game of Kaali Teeri: its done hands and its seats."""

    game_id: str
    hands: tuple[KtHandFact, ...]
    seats: tuple[KtSeatFact, ...]


@dataclass(frozen=True)
class KtCareerFacts:
    """Some players, and every seat and done hand of every game they sat in (others' too: crew)."""

    player_ids: tuple[str, ...]
    hands: tuple[KtHandFact, ...] = ()
    seats: tuple[KtSeatFact, ...] = ()


@dataclass(frozen=True)
class _Ended:
    """A seat of an ended game."""

    seat: KtSeatFact
    ended_at: int
    place: int

    @property
    def won(self) -> bool:
        """First place (a tie for first is a win for each)."""
        return self.place == 1


@dataclass(frozen=True)
class _HandCounts:
    """One player's hand ladders: hands played, bids made, partner wins, longest made run."""

    played: int
    made: int
    partner: int
    longest: int


def deck_of(seats: int) -> int:
    """The points in play at a table of `seats`: two decks from 7 seats."""
    return deck_total(decks_for(seats))


def _big(h: KtHandFact) -> bool:
    """At least 80% of the deck: 200 with one deck, 400 with two."""
    return h.bid * 5 >= h.deck * 4


def _at_hand(
    h: KtHandFact, player_id: str, code: str, data: dict[str, Any] | None = None
) -> Earned:
    return Earned(code, player_id, h.game_id, h.hand_index, None, h.done_at, data or {})


def _made_moments(h: KtHandFact) -> list[Earned]:
    b, bid = h.bidder_id, {"bid": h.bid}
    out = [_at_hand(h, b, "high_roller", bid)] if _big(h) else []
    if h.bid == h.deck:
        out.append(_at_hand(h, b, "all_in", bid))
    if not h.partner_ids:
        out.append(_at_hand(h, b, "lone_wolf"))
    if h.forced:
        out.append(_at_hand(h, b, "stuck_with_it", bid))
    out += [
        _at_hand(h, p, "double_agent", {"slots": h.slots[p]})
        for p in h.partner_ids
        if h.slots.get(p, 0) >= 2
    ]
    return out


def _missed_moments(h: KtHandFact) -> list[Earned]:
    b, bid = h.bidder_id, {"bid": h.bid}
    out = [_at_hand(h, b, "so_close", bid)] if h.bid - h.team_points == SO_CLOSE_POINTS else []
    if _big(h):
        out.append(_at_hand(h, b, "overcooked", bid))
        out += [_at_hand(h, p, "giant_killer", bid) for p in h.defenders]
    return out


def hand_moments(h: KtHandFact) -> list[Earned]:
    """The moments of one hand: the bidder's, the bidding side's and the defenders'."""
    out = _made_moments(h) if h.made else _missed_moments(h)
    if h.sweep == Sweep.BIDDERS:
        out += [_at_hand(h, p, "clean_sweep") for p in h.side]
    if h.sweep == Sweep.DEFENDERS:
        out += [_at_hand(h, p, "brick_wall") for p in h.defenders]
    return out


def _ended(seats: Iterable[KtSeatFact]) -> list[_Ended]:
    return [
        _Ended(s, s.ended_at, s.place)
        for s in seats
        if s.ended_at is not None and s.place is not None
    ]


def _at_end(e: _Ended, code: str, data: dict[str, Any] | None = None) -> Earned:
    return Earned(code, e.seat.player_id, e.seat.game_id, None, None, e.ended_at, data or {})


def _ranks_after_each_hand(hands: Sequence[KtHandFact]) -> list[dict[str, int]]:
    """Competition ranks (1, 1, 3) by running total after each hand of one game."""
    total: Counter[str] = Counter()
    out: list[dict[str, int]] = []
    for h in sorted(hands, key=lambda x: x.hand_index):
        total.update(h.scores)
        values = list(total.values())
        out.append({p: 1 + sum(1 for x in values if x > v) for p, v in total.items()})
    return out


def _was_last(player_id: str, steps: Sequence[dict[str, int]]) -> bool:
    """Last with someone ahead, after a hand at or past halfway and before the final one."""
    n = len(steps)
    return any(
        (k + 1) * 2 >= n and k < n - 1 and 1 < max(r.values()) == r.get(player_id)
        for k, r in enumerate(steps)
    )


def _winner_codes(e: _Ended, steps: Sequence[dict[str, int]]) -> list[Earned]:
    out = [_at_end(e, "winner", {"points": e.seat.points})]
    if len(steps) < SHAPE_MIN_HANDS:
        return out
    pid = e.seat.player_id
    if _was_last(pid, steps):
        out.append(_at_end(e, "comeback"))
    if all(r.get(pid) == 1 for r in steps):
        out.append(_at_end(e, "wire_to_wire"))
    return out


def _seat_codes(e: _Ended, hands: int, table: int) -> list[Earned]:
    s = e.seat
    checks: tuple[tuple[bool, str, dict[str, Any]], ...] = (
        (s.points >= THOUSANDAIRE_POINTS, "thousandaire", {"points": s.points}),
        (
            s.hands_bid >= NEVER_WRONG_MIN_BIDS and s.bids_made == s.hands_bid,
            "never_wrong",
            {"bids": s.hands_bid},
        ),
        (s.partner_made >= KINGMAKER_MIN_HANDS, "kingmaker", {"hands": s.partner_made}),
        (hands >= SHAPE_MIN_HANDS and s.points == 0, "blanked", {}),
        (table == FULL_HOUSE_SEATS, "full_house", {}),
        (hands >= MARATHON_HANDS, "marathon", {"hands": hands}),
    )
    return [_at_end(e, code, data) for ok, code, data in checks if ok]


def _wooden_spoon(ended: Sequence[_Ended]) -> list[Earned]:
    """Last place (ties included), when not everyone shares it."""
    worst = max(e.place for e in ended)
    return [_at_end(e, "wooden_spoon") for e in ended if worst > 1 and e.place == worst]


def _finish(facts: KtGameFacts, tz: ZoneInfo) -> list[Earned]:
    """The game-end codes of an ended game, earned at its end."""
    ended = _ended(facts.seats)
    if not ended:
        return []
    steps = _ranks_after_each_hand(facts.hands)
    out: list[Earned] = []
    for e in ended:
        if e.won:
            out += _winner_codes(e, steps)
        out += _seat_codes(e, len(facts.hands), len(facts.seats))
    out += _wooden_spoon(ended)
    return out + [_at_end(e, "night_owl") for e in ended if is_night(e.ended_at, tz)]


def game_achievements(facts: KtGameFacts, tz: ZoneInfo) -> list[Earned]:
    """Hand moments and game-end achievements of one game; "night" is judged in `tz`."""
    return [m for h in facts.hands for m in hand_moments(h)] + _finish(facts, tz)


def _in_order(hands: Iterable[KtHandFact]) -> list[KtHandFact]:
    """Hands in play order: when they were done, then game, then index."""
    return sorted(hands, key=lambda h: (h.done_at, h.game_id, h.hand_index))


def _played_games(seats: Iterable[KtSeatFact]) -> dict[str, list[PlayedGame]]:
    """Each player's ended games in (ended_at, game_id) order, with everyone at that table."""
    ended = _ended(seats)
    tables: dict[str, list[str]] = {}
    for e in ended:
        tables.setdefault(e.seat.game_id, []).append(e.seat.player_id)
    out: dict[str, list[PlayedGame]] = {}
    for e in sorted(ended, key=lambda x: (x.ended_at, x.seat.game_id)):
        game = PlayedGame(
            e.seat.player_id, e.seat.game_id, e.ended_at, e.won, tuple(tables[e.seat.game_id])
        )
        out.setdefault(e.seat.player_id, []).append(game)
    return out


def _bid_codes(h: KtHandFact, player_id: str, made: int, run: int) -> list[Earned]:
    """made_<n> at the n-th made bid; streak_<n> each time a run of made bids reaches n."""
    if not h.made:
        return []
    out = [_at_hand(h, player_id, f"made_{made}", {"n": made})] if made in MADE_STEPS else []
    if run in STREAK_STEPS:
        out.append(_at_hand(h, player_id, f"streak_{run}", {"n": run}))
    return out


def _partners_in_crime(
    h: KtHandFact, player_id: str, mates: Counter[str], credited: set[str]
) -> list[Earned]:
    """Once per other person and one per hand: a second person waits for their next win together."""
    others = [o for o in h.side if o != player_id]
    mates.update(others)
    due = sorted(o for o in others if o not in credited and mates[o] >= PARTNERS_IN_CRIME_HANDS)
    if not due:
        return []
    credited.add(due[0])
    return [_at_hand(h, player_id, "partners_in_crime", {"with": due[0]})]


def _hand_career(player_id: str, hands: Sequence[KtHandFact]) -> tuple[list[Earned], _HandCounts]:
    """One player's hand-counted codes over `hands` in play order, and the counts behind them."""
    out: list[Earned] = []
    played = made = partner = run = longest = 0
    mates: Counter[str] = Counter()
    credited: set[str] = set()
    for h in (x for x in hands if player_id in x.scores):
        played += 1
        if played in HANDS_STEPS:
            out.append(_at_hand(h, player_id, f"hands_{played}", {"n": played}))
        if h.bidder_id == player_id:
            run = run + 1 if h.made else 0
            longest = max(longest, run)
            made += h.made
            out += _bid_codes(h, player_id, made, run)
        if not h.made or player_id not in h.side:
            continue
        if player_id in h.partner_ids:
            partner += 1
            if partner in PARTNER_STEPS:
                out.append(_at_hand(h, player_id, f"partner_{partner}", {"n": partner}))
        out += _partners_in_crime(h, player_id, mates, credited)
    return out, _HandCounts(played, made, partner, longest)


def career_achievements(facts: KtCareerFacts) -> list[Earned]:
    """Every career achievement the players in `facts` hold (theirs only, never their tables')."""
    games = _played_games(facts.seats)
    hands = _in_order(facts.hands)
    out: list[Earned] = []
    for pid in facts.player_ids:
        out += shared_career(pid, games.get(pid, []), games_steps=GAMES_STEPS)
        out += _hand_career(pid, hands)[0]
    return out


def ladder_counts(facts: KtCareerFacts, player_id: str) -> dict[str, int]:
    """Each ladder's count for one player, counted the way its codes are earned."""
    shared = shared_ladders(player_id, _played_games(facts.seats).get(player_id, []))
    _, h = _hand_career(player_id, _in_order(facts.hands))
    counts = {
        **shared,
        "hands": h.played,
        "made": h.made,
        "partner": h.partner,
        "streak": h.longest,
    }
    return {ladder: counts[ladder] for ladder in LADDER_IDS}
