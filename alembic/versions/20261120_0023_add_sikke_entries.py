"""add sikke entries

What and why: Sikke, the app's coins (rewards plan §9). The server decides every credit in the
same transaction as the game write, so `sikke_entries` holds one row per (player, key): finishes,
wins, achievements, Kursi takes and defences, and weekly streaks. Rows are recomputed, never typed
in; a game's or achievement's rows go with it (cascade). Fill it once with `recompute-sikke`.
Rollback: `alembic downgrade -1` drops the table; `recompute-sikke` rebuilds it after an upgrade.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak')"
_GAMES = "game IS NULL OR game IN ('kachuful', 'spades', 'kaali-teeri', 'ishaara')"


def upgrade() -> None:
    op.create_table(
        "sikke_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("game", sa.Text(), nullable=True),
        sa.Column("game_id", sa.Uuid(), nullable=True),
        sa.Column("achievement_id", sa.Uuid(), nullable=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(_KINDS, name=op.f("ck_sikke_entries_kind")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_sikke_entries_amount")),
        sa.CheckConstraint(_GAMES, name=op.f("ck_sikke_entries_game")),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_sikke_entries_player_id")
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_sikke_entries_game_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["achievement_id"],
            ["achievements.id"],
            name=op.f("fk_sikke_entries_achievement_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sikke_entries")),
    )
    op.create_index(
        "uq_sikke_entries_player_id_key", "sikke_entries", ["player_id", "key"], unique=True
    )
    op.create_index(
        "ix_sikke_entries_player_id_earned_at",
        "sikke_entries",
        ["player_id", sa.literal_column("earned_at DESC"), sa.literal_column("id DESC")],
    )
    op.create_index("ix_sikke_entries_game_id", "sikke_entries", ["game_id"])
    op.create_index("ix_sikke_entries_achievement_id", "sikke_entries", ["achievement_id"])


def downgrade() -> None:
    op.drop_table("sikke_entries")
