"""add player emails and photos

What and why: player identity and photos (contract change 2026-10-31). `players.email` (citext,
unique among live players) identifies a person together with the display name; `photo_rev` versions
the photo URL; `player_photos` holds one re-encoded 256x256 WebP per player.
Rollback: `alembic downgrade -1` drops the column, index and table; every email and photo is lost.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_photos",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_player_photos_player_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("player_id", name=op.f("pk_player_photos")),
    )
    op.add_column("players", sa.Column("email", postgresql.CITEXT(), nullable=True))
    op.add_column(
        "players", sa.Column("photo_rev", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_index(
        "uq_players_email_active",
        "players",
        ["email"],
        unique=True,
        postgresql_where=sa.text("merged_into IS NULL AND email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_players_email_active",
        table_name="players",
        postgresql_where=sa.text("merged_into IS NULL AND email IS NOT NULL"),
    )
    op.drop_column("players", "photo_rev")
    op.drop_column("players", "email")
    op.drop_table("player_photos")
