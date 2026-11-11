"""Ranking the Kaali Teeri board by points, wins or made %. Competition ranks (1, 1, 3)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class KtSort(StrEnum):
    """What the board ranks by."""

    POINTS = "points"
    WINS = "wins"
    MADE = "made"


@dataclass(frozen=True)
class KtTally:
    """One player's totals over a period."""

    player_id: str
    games: int
    wins: int
    points: int
    hands_bid: int
    bids_made: int

    @property
    def made_rate(self) -> float | None:
        """Bids made out of hands bid; None with no bids."""
        return self.bids_made / self.hands_bid if self.hands_bid else None


@dataclass(frozen=True)
class KtBoardRow:
    """A ranked tally; `rank` is None when the sort value doesn't exist (made % with no bids)."""

    rank: int | None
    value: float
    tally: KtTally


def _value(sort: KtSort) -> Callable[[KtTally], float | None]:
    if sort == KtSort.POINTS:
        return lambda t: float(t.points)
    if sort == KtSort.WINS:
        return lambda t: float(t.wins)
    return lambda t: t.made_rate


def rank_board(tallies: Sequence[KtTally], sort: KtSort) -> list[KtBoardRow]:
    """Best first; ties share a rank and are listed by points, then id. Unranked rows go last."""
    value = _value(sort)
    ordered = sorted(
        tallies,
        key=lambda t: (value(t) is None, -(value(t) or 0.0), -t.points, t.player_id),
    )
    rows: list[KtBoardRow] = []
    for k, tally in enumerate(ordered):
        v = value(tally)
        if v is None:
            rows.append(KtBoardRow(rank=None, value=0.0, tally=tally))
            continue
        rank = rows[-1].rank if rows and rows[-1].value == v else k + 1
        rows.append(KtBoardRow(rank=rank, value=v, tally=tally))
    return rows
