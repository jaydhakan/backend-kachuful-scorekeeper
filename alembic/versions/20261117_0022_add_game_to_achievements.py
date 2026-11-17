"""add game to achievements, activity and closed periods

What and why: every game (Kachuful, Spades, Kaali Teeri, Ishaara) now earns achievements and
awards. Codes such as `winner` and `games_10` exist in several games, and career and period rows
have no game row to tell them apart, so `achievements`, `activity` and `closed_periods` gain a
`game` column (default `kachuful`, which backfills every existing row). The achievements unique key
and per-player paging index lead with it, the feed gets a per-game index, and a day or month closes
once per game: `closed_periods` is keyed by (game, period).
Rollback: `alembic downgrade -1` deletes every row whose game isn't `kachuful` (achievements, their
feed items, other feed items and closed periods), restores the old keys and drops the columns.
Those rows can be rebuilt with `recompute-achievements` and `close-day`/`close-season --since`.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GAMES = "game IN ('kachuful', 'spades', 'kaali-teeri', 'ishaara')"
_TABLES = ("achievements", "activity", "closed_periods")


def _game_column() -> sa.Column[str]:
    return sa.Column("game", sa.Text(), nullable=False, server_default=sa.text("'kachuful'"))


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, _game_column())
    for table in ("achievements", "activity"):
        op.create_check_constraint(op.f(f"ck_{table}_game"), table, _GAMES)
    op.drop_index("uq_achievements_key", table_name="achievements")
    op.create_index(
        "uq_achievements_key",
        "achievements",
        ["game", "player_id", "code", "game_id", "round_index", "period"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_achievements_player_id_earned_at", table_name="achievements")
    op.create_index(
        "ix_achievements_player_id_game_earned_at",
        "achievements",
        ["player_id", "game", sa.literal_column("earned_at DESC"), sa.literal_column("id DESC")],
    )
    op.create_index(
        "ix_activity_game_created_at_id",
        "activity",
        ["game", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
    )
    op.drop_constraint("pk_closed_periods", "closed_periods", type_="primary")
    op.create_primary_key("pk_closed_periods", "closed_periods", ["game", "period"])


def downgrade() -> None:
    op.execute("DELETE FROM achievements WHERE game <> 'kachuful'")
    op.execute("DELETE FROM activity WHERE game <> 'kachuful'")
    op.execute("DELETE FROM closed_periods WHERE game <> 'kachuful'")
    op.drop_constraint("pk_closed_periods", "closed_periods", type_="primary")
    op.create_primary_key("pk_closed_periods", "closed_periods", ["period"])
    op.drop_index("ix_activity_game_created_at_id", table_name="activity")
    op.drop_index("ix_achievements_player_id_game_earned_at", table_name="achievements")
    op.create_index(
        "ix_achievements_player_id_earned_at",
        "achievements",
        ["player_id", sa.literal_column("earned_at DESC"), sa.literal_column("id DESC")],
    )
    op.drop_index("uq_achievements_key", table_name="achievements")
    op.create_index(
        "uq_achievements_key",
        "achievements",
        ["player_id", "code", "game_id", "round_index", "period"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    for table in _TABLES:
        op.drop_column(table, "game")
