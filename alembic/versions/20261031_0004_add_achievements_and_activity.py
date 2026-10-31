"""add achievements, activity and closed periods

What and why: B6/B7 records. `achievements` holds round, game, career, daily and season rows (one
per key, NULLs equal); `activity` is the feed; `closed_periods` freezes finalised days and seasons;
`player_day.pos_points` lets the "points earned" milestones be checked without scanning rounds.
Rollback: `alembic downgrade -1` drops the three tables and the column; every earned achievement,
feed item and closed day or season is lost (they can be rebuilt by re-uploading or re-closing).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "closed_periods",
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "closed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("kind IN ('day', 'season')", name=op.f("ck_closed_periods_kind")),
        sa.PrimaryKeyConstraint("period", name=op.f("pk_closed_periods")),
    )
    op.create_table(
        "achievements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=True),
        sa.Column("round_index", sa.Integer(), nullable=True),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_achievements_game_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_achievements_player_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_achievements")),
    )
    op.create_index("ix_achievements_code_period", "achievements", ["code", "period"], unique=False)
    op.create_index(
        "ix_achievements_earned_at_id",
        "achievements",
        [sa.literal_column("earned_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index("ix_achievements_game_id", "achievements", ["game_id"], unique=False)
    op.create_index(
        "ix_achievements_player_id_earned_at",
        "achievements",
        ["player_id", sa.literal_column("earned_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_achievements_key",
        "achievements",
        ["player_id", "code", "game_id", "round_index", "period"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "activity",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=True),
        sa.Column("game_id", sa.Uuid(), nullable=True),
        sa.Column("achievement_id", sa.Uuid(), nullable=True),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined')",
            name=op.f("ck_activity_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["achievement_id"],
            ["achievements.id"],
            name=op.f("fk_activity_achievement_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_activity_game_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], name=op.f("fk_activity_player_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    op.create_index(
        "ix_activity_created_at_id",
        "activity",
        [sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index("ix_activity_game_id", "activity", ["game_id"], unique=False)
    op.add_column(
        "player_day", sa.Column("pos_points", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("player_day", "pos_points")
    op.drop_index("ix_activity_game_id", table_name="activity")
    op.drop_index("ix_activity_created_at_id", table_name="activity")
    op.drop_table("activity")
    op.drop_index(
        "uq_achievements_key", table_name="achievements", postgresql_nulls_not_distinct=True
    )
    op.drop_index("ix_achievements_player_id_earned_at", table_name="achievements")
    op.drop_index("ix_achievements_game_id", table_name="achievements")
    op.drop_index("ix_achievements_earned_at_id", table_name="achievements")
    op.drop_index("ix_achievements_code_period", table_name="achievements")
    op.drop_table("achievements")
    op.drop_table("closed_periods")
