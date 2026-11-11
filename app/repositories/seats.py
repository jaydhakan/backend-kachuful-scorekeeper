"""Where each game kind seats registry players. Player-wide queries read every table listed here.

Adding a game's seat table is one `SEAT_TABLES` entry (plus its reseat in `services/merge.py`).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, FromClause, Select, Subquery, exists, or_, select, union_all
from sqlalchemy.orm import QueryableAttribute

from app.db.models import Game, GamePlayer, IshaaraRole, KtGamePlayer
from app.domain.enums import GameKind


@dataclass(frozen=True)
class SeatTable:
    """One row per (game, seated player) of a kind: `game_id`, `player_id` and `last_done_at` columns.

    A table without its own `last_done_at` (`own_last_done_at=False`) takes the game row's.
    """

    kind: GameKind
    table: FromClause
    own_last_done_at: bool = True


SEAT_TABLES: tuple[SeatTable, ...] = (
    SeatTable(kind=GameKind.KACHUFUL, table=GamePlayer.__table__),
    SeatTable(kind=GameKind.KAALI_TEERI, table=KtGamePlayer.__table__),
    # Every seat from creation on; its last reveal is the game row's `last_done_at`.
    SeatTable(kind=GameKind.ISHAARA, table=IshaaraRole.__table__, own_last_done_at=False),
)


def seated_anywhere(
    player_id: QueryableAttribute[uuid.UUID] | ColumnElement[Any] | uuid.UUID,
) -> ColumnElement[bool]:
    """Whether the player sits in a game of any kind (one EXISTS per table, each indexed)."""
    return or_(*(exists().where(seats.table.c.player_id == player_id) for seats in SEAT_TABLES))


def seat_rows(name: str = "seats") -> Subquery:
    """Every kind's seats as (game_id, player_id, last_done_at); Postgres pushes filters into each."""
    return union_all(*(_seat_select(seats) for seats in SEAT_TABLES)).subquery(name)


def _seat_select(seats: SeatTable) -> Select[Any]:
    t = seats.table
    if seats.own_last_done_at:
        return select(t.c.game_id, t.c.player_id, t.c.last_done_at)
    return select(t.c.game_id, t.c.player_id, Game.last_done_at).join(Game, Game.id == t.c.game_id)
