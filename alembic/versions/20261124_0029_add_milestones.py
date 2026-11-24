"""add milestones

What and why: group milestones and firsts (rewards finish §6). `milestones` is derived: every write
that changes the finished games or their achievements recomputes it in the same transaction, and
`recompute-milestones` rebuilds it. The activity feed gains the `milestone` kind.
Backfill with `recompute-milestones` (no feed items).
Rollback: `alembic downgrade -1` drops the table, deletes `milestone` feed items and restores the
previous kind check.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVITY_OLD = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', 'night', "
    "'gift')"
)
_ACTIVITY_NEW = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', 'night', "
    "'gift', 'milestone')"
)
_GAMES = "game IN ('kachuful', 'spades', 'kaali-teeri', 'ishaara')"


def _swap_activity_check(condition: str) -> None:
    op.drop_constraint(op.f("ck_activity_kind"), "activity", type_="check")
    op.create_check_constraint(op.f("ck_activity_kind"), "activity", condition)


def upgrade() -> None:
    op.create_table(
        "milestones",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("game", sa.Text(), nullable=True),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("players", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('group', 'first')", name=op.f("ck_milestones_kind")),
        sa.CheckConstraint(f"game IS NULL OR {_GAMES}", name=op.f("ck_milestones_game")),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_milestones_game_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_milestones")),
    )
    op.create_index(
        "ix_milestones_at_key",
        "milestones",
        [sa.literal_column("at DESC"), sa.literal_column("key DESC")],
    )
    _swap_activity_check(_ACTIVITY_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM activity WHERE kind = 'milestone'")
    _swap_activity_check(_ACTIVITY_OLD)
    op.drop_table("milestones")
