"""add nights

What and why: nights (rewards plan §7.2). A night is a connected group of finished games that share
a player with gaps of three hours or less; `close-nights` stores each one once its last game is
three hours old (its Wrapped story in `data`), posts a `night` feed item and pays the night's Sitara
(`sitara` Sikke entries). GIN indexes find a player's nights and the night a game belongs to.
Backfill with `close-nights --all`, then `recompute-sikke`.
Rollback: `alembic downgrade -1` drops the table, deletes `night` feed items and `sitara` entries,
and restores the previous kind checks.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVITY_OLD = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown')"
)
_ACTIVITY_NEW = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', 'night')"
)
_SIKKE_OLD = (
    "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak', 'bounty')"
)
_SIKKE_NEW = (
    "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak', 'bounty', "
    "'sitara')"
)


def _swap_check(table: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_kind"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_kind"), table, condition)


def upgrade() -> None:
    op.create_table(
        "nights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("players", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("games", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nights")),
    )
    op.create_index("ix_nights_players", "nights", ["players"], postgresql_using="gin")
    op.create_index("ix_nights_games", "nights", ["games"], postgresql_using="gin")
    op.create_index(
        "ix_nights_ended_at_id",
        "nights",
        [sa.literal_column("ended_at DESC"), sa.literal_column("id DESC")],
    )
    _swap_check("activity", _ACTIVITY_NEW)
    _swap_check("sikke_entries", _SIKKE_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM sikke_entries WHERE kind = 'sitara'")
    op.execute("DELETE FROM activity WHERE kind = 'night'")
    _swap_check("sikke_entries", _SIKKE_OLD)
    _swap_check("activity", _ACTIVITY_OLD)
    op.drop_table("nights")
