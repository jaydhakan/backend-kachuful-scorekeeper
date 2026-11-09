"""Where each game kind seats registry players. Player-wide queries read every table listed here.

Adding a game's seat table is one `SEAT_TABLES` entry (plus its reseat in `services/merge.py`).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, FromClause, Subquery, exists, or_, select, union_all
from sqlalchemy.orm import QueryableAttribute

from app.db.models import GamePlayer
from app.domain.enums import GameKind


@dataclass(frozen=True)
class SeatTable:
    """One row per (game, seated player) of a kind: `game_id`, `player_id`, `last_done_at` columns."""

    kind: GameKind
    table: FromClause


SEAT_TABLES: tuple[SeatTable, ...] = (
    SeatTable(kind=GameKind.KACHUFUL, table=GamePlayer.__table__),
)


def seated_anywhere(
    player_id: QueryableAttribute[uuid.UUID] | ColumnElement[Any] | uuid.UUID,
) -> ColumnElement[bool]:
    """Whether the player sits in a game of any kind (one EXISTS per table, each indexed)."""
    return or_(*(exists().where(seats.table.c.player_id == player_id) for seats in SEAT_TABLES))


def seat_rows(name: str = "seats") -> Subquery:
    """Every kind's seats as (game_id, player_id, last_done_at); Postgres pushes filters into each."""
    return union_all(
        *(
            select(seats.table.c.game_id, seats.table.c.player_id, seats.table.c.last_done_at)
            for seats in SEAT_TABLES
        )
    ).subquery(name)
