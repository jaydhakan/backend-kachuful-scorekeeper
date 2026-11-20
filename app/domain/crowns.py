"""Crowns (rewards plan §6): who holds each title now, and what changed since the stored holders.

Medals are this month's podium, the Kursi its chain, the Sitara tonight's points leader, the
Legend the most season titles. Pure: the service stores the rows, reigns and feed items.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.domain.leaderboard import Board

SITARA_MIN_GAMES = 2
"""The Sitara needs this many games, so the only game of a night crowns nobody."""
LEGEND_MIN_TITLES = 2
"""The Legend needs at least two season titles (the app's `legendOf`)."""


class CrownKind(StrEnum):
    """A title; `crowns.kind` is checked against these values."""

    MEDAL1 = "medal1"
    MEDAL2 = "medal2"
    MEDAL3 = "medal3"
    KURSI = "kursi"
    SITARA = "sitara"
    LEGEND = "legend"


ReignSort = Literal["recent", "defences"]
"""How closed reigns are listed: newest end first, or most defences first."""

MEDALS = (CrownKind.MEDAL1, CrownKind.MEDAL2, CrownKind.MEDAL3)


@dataclass(frozen=True)
class Crown:
    """Who holds a title (sorted ids; empty when nobody), since when and its defences."""

    kind: CrownKind
    holders: tuple[str, ...]
    since: int | None = None
    since_game: str | None = None
    defences: int = 0


@dataclass(frozen=True)
class CrownChange:
    """A title changing hands; `defences` are the outgoing reign's."""

    kind: CrownKind
    from_: tuple[str, ...]
    to: tuple[str, ...]
    defences: int


def medal_holders(board: Board) -> dict[CrownKind, tuple[str, ...]]:
    """Ranks 1-3 of a season board, qualified rows only (ties share a medal)."""
    return {
        kind: tuple(sorted(r.player_id for r in board.rows if r.qualified and r.rank == n))
        for n, kind in enumerate(MEDALS, start=1)
    }


def sitara_of(points: Mapping[str, int], games: int) -> tuple[str, ...]:
    """Tonight's leaders on points (the app's `sitaraOf`): ties share; nobody at 0 or less."""
    if games < SITARA_MIN_GAMES:
        return ()
    best = max([0, *points.values()])
    return tuple(sorted(pid for pid, v in points.items() if v == best)) if best > 0 else ()


def legend_of(champions: Iterable[str]) -> tuple[str, ...]:
    """Whoever holds the most season titles, at least two (ties share)."""
    n = Counter(champions)
    best = max(n.values(), default=0)
    if best < LEGEND_MIN_TITLES:
        return ()
    return tuple(sorted(pid for pid, v in n.items() if v == best))


def crown_changes(
    stored: Mapping[CrownKind, Crown], desired: Mapping[CrownKind, Crown]
) -> list[CrownChange]:
    """Titles whose holders differ from the stored ones, in `CrownKind` order."""
    out: list[CrownChange] = []
    for kind in CrownKind:
        want = desired.get(kind)
        have = stored.get(kind)
        before = () if have is None else have.holders
        after = () if want is None else want.holders
        if before != after:
            out.append(CrownChange(kind, before, after, 0 if have is None else have.defences))
    return out
