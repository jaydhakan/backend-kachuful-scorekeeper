"""Kaali Teeri daily awards and seasons (app: src/games/kaali-teeri/domain/awards.ts). Pure.

Both read the hands done in their window. Seasons rank players by win rate
(`domain.progression.win_rate_standings`, at least SEASON_MIN_GAMES finished games in the month);
the podium, Sharpest Bidder, Kingmaker and Iron Player go to the ranked only. Ties share an award.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.kaali_teeri.achievements import KtHandFact
from app.domain.progression import (
    Award,
    StandingRow,
    best_of,
    climber_awards,
    iron_awards,
    podium_awards,
)

SEASON_MIN_GAMES = 8
# Bids a day's Sharpest and a season's Sharpest Bidder need.
DAY_SHARP_BIDS = 3
SEASON_SHARP_BIDS = 15
# Partner wins and beaten bids a day's Partner and Giant Killer need.
DAY_PARTNER_MIN = 2
DAY_KILLER_MIN = 2

DAILY_CODES: frozenset[str] = frozenset(
    {
        "day_star",
        "day_bid",
        "day_partner",
        "day_sharp",
        "day_killer",
        "day_overcooked",
        "day_grinder",
    }
)
SEASON_CODES: frozenset[str] = frozenset(
    {
        "season_champion",
        "season_runner_up",
        "season_third",
        "season_sharpest",
        "season_kingmaker",
        "season_iron",
        "season_climber",
    }
)


@dataclass(frozen=True)
class KtDayFacts:
    """One APP_TZ day [start_ms, end_ms) of play: the hands done in it."""

    start_ms: int
    end_ms: int
    hands: tuple[KtHandFact, ...] = ()


@dataclass(frozen=True)
class KtMonthFacts:
    """One APP_TZ month [start_ms, end_ms) of play: the hands done in it."""

    start_ms: int
    end_ms: int
    hands: tuple[KtHandFact, ...] = ()


@dataclass(frozen=True)
class _Tally:
    """One player's hands in a window."""

    points: int = 0
    hands: int = 0
    bids: int = 0
    made: int = 0
    # Hands won as a called partner, and bids beaten as a defender.
    partner_won: int = 0
    beaten: int = 0

    def plus(self, h: KtHandFact, player_id: str) -> "_Tally":
        """This tally with one more hand of `player_id`'s."""
        bidder = h.bidder_id == player_id
        partner = player_id in h.partner_ids
        return _Tally(
            points=self.points + h.scores[player_id],
            hands=self.hands + 1,
            bids=self.bids + bidder,
            made=self.made + (bidder and h.made),
            partner_won=self.partner_won + (h.made and partner),
            beaten=self.beaten + (not h.made and not bidder and not partner),
        )

    @property
    def rate(self) -> float:
        """Bids made over bids."""
        return self.made / self.bids


def _within(hands: Iterable[KtHandFact], start_ms: int, end_ms: int) -> list[KtHandFact]:
    return [h for h in hands if start_ms <= h.done_at < end_ms]


def _tallies(hands: Iterable[KtHandFact]) -> dict[str, _Tally]:
    out: dict[str, _Tally] = {}
    for h in hands:
        for pid in h.scores:
            out[pid] = out.get(pid, _Tally()).plus(h, pid)
    return out


def _biggest_bid(hands: Sequence[KtHandFact], code: str) -> list[Award]:
    """The biggest bid among `hands`: every bidder who bid it, each at their earliest such hand."""
    if not hands:
        return []
    top = max(h.bid for h in hands)
    first: dict[str, KtHandFact] = {}
    for h in sorted(hands, key=lambda x: (x.done_at, x.game_id, x.hand_index)):
        if h.bid == top:
            first.setdefault(h.bidder_id, h)
    return [
        Award(code, pid, top, game_id=h.game_id, round_index=h.hand_index)
        for pid, h in sorted(first.items())
    ]


def daily_awards(facts: KtDayFacts) -> list[Award]:
    """The day's awards, in the app's order."""
    hands = _within(facts.hands, facts.start_ms, facts.end_ms)
    t = _tallies(hands).items()
    return [
        *best_of(((p, x.points) for p, x in t), "day_star", ok=lambda v: v > 0),
        *_biggest_bid([h for h in hands if h.made], "day_bid"),
        *best_of(
            ((p, x.partner_won) for p, x in t), "day_partner", ok=lambda v: v >= DAY_PARTNER_MIN
        ),
        *best_of(
            ((p, x.rate) for p, x in t if x.bids >= DAY_SHARP_BIDS), "day_sharp", ok=lambda v: v > 0
        ),
        *best_of(((p, x.beaten) for p, x in t), "day_killer", ok=lambda v: v >= DAY_KILLER_MIN),
        *_biggest_bid([h for h in hands if not h.made], "day_overcooked"),
        *best_of(((p, x.hands) for p, x in t), "day_grinder"),
    ]


def _season_own(ranked: Sequence[StandingRow], t: dict[str, _Tally]) -> list[Award]:
    """Sharpest Bidder (15+ bids) and Kingmaker among the ranked."""
    sharp = [(r.player_id, t[r.player_id]) for r in ranked if r.player_id in t]
    return [
        *best_of(
            ((p, x.rate) for p, x in sharp if x.bids >= SEASON_SHARP_BIDS),
            "season_sharpest",
            ok=lambda v: v > 0,
        ),
        *best_of(
            ((r.player_id, t.get(r.player_id, _Tally()).partner_won) for r in ranked),
            "season_kingmaker",
            ok=lambda v: v > 0,
        ),
    ]


def season_awards(
    standings: Sequence[StandingRow], last: Sequence[StandingRow], facts: KtMonthFacts
) -> list[Award]:
    """The podium, Sharpest Bidder, Kingmaker, Iron Player, then the climber."""
    t = _tallies(_within(facts.hands, facts.start_ms, facts.end_ms))
    ranked = [r for r in standings if r.qualified]
    return [
        *podium_awards(standings),
        *_season_own(ranked, t),
        *iron_awards(standings),
        *climber_awards(standings, last),
    ]
