"""The Kursi (rewards plan §6.3): one chair per game, taken by finishing above its holder.

Mirrors the app's `src/progression/kursi.ts`; parity is the `kursi-*` golden fixtures.
"""

from dataclasses import dataclass, replace
from typing import Literal

from app.domain.placed import PlacedGame, PlacedSeat

KURSI_MIN_TABLE = 3
"""A game counts with three or more at the table: two friends can't trade it between them."""


@dataclass(frozen=True)
class Kursi:
    """The holder, or a side that took it together (kept while its best-placed holder is unbeaten)."""

    holders: tuple[str, ...]
    since: int
    since_game: str
    defences: int


@dataclass(frozen=True)
class Reign:
    holders: tuple[str, ...]
    since: int
    since_game: str
    defences: int
    until: int | None


@dataclass(frozen=True)
class KursiEvent:
    kind: Literal["first", "taken", "defended", "kept"]
    to: tuple[str, ...] = ()
    from_: tuple[str, ...] = ()


def _takers(seats: list[PlacedSeat], teams: bool) -> tuple[str, ...]:
    if not seats:
        return ()
    best = min(s.place for s in seats)
    top = sorted((s for s in seats if s.place == best), key=lambda s: s.seat)
    return tuple(s.player_id for s in (top if teams else top[:1]))


def step_kursi(k: Kursi | None, g: PlacedGame) -> tuple[Kursi | None, KursiEvent]:
    """The chair after one finished game, and what happened to it."""
    if len(g.seats) < KURSI_MIN_TABLE:
        return k, KursiEvent("kept")
    if k is None:
        to = _takers(list(g.seats), g.teams)
        return Kursi(to, g.ended_at, g.game_id, 0), KursiEvent("first", to=to)
    seated = [s for s in g.seats if s.player_id in k.holders]
    if not seated:
        return k, KursiEvent("kept")
    holder_place = min(s.place for s in seated)
    above = [s for s in g.seats if s.player_id not in k.holders and s.place < holder_place]
    if not above:
        return replace(k, defences=k.defences + 1), KursiEvent("defended")
    to = _takers(above, g.teams)
    return Kursi(to, g.ended_at, g.game_id, 0), KursiEvent("taken", to=to, from_=k.holders)


def kursi_from(games: list[PlacedGame]) -> tuple[Kursi | None, list[Reign]]:
    """The chair now and every reign, from finished games played oldest first."""
    kursi: Kursi | None = None
    reigns: list[Reign] = []
    for g in sorted(games, key=lambda x: (x.ended_at, x.game_id)):
        kursi, event = step_kursi(kursi, g)
        if event.kind in ("first", "taken") and kursi is not None:
            if reigns:
                reigns[-1] = replace(reigns[-1], until=g.ended_at)
            reigns.append(Reign(kursi.holders, kursi.since, kursi.since_game, 0, None))
        elif event.kind == "defended":
            reigns[-1] = replace(reigns[-1], defences=reigns[-1].defences + 1)
    return kursi, reigns
