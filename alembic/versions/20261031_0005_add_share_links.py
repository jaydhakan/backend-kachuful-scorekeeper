"""add share links

What and why: B8 live and recap links. Tokens are capabilities shown again as QR codes, so they
are stored plain (plan §5.1); the partial index finds a game's active link of each kind.
Rollback: `alembic downgrade -1` drops the table; every shared link stops working.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('live', 'recap')", name=op.f("ck_share_links_kind")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_share_links_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_share_links_game_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_share_links")),
    )
    op.create_index(
        "ix_share_links_game_id_kind_active",
        "share_links",
        ["game_id", "kind"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_share_links_game_id_kind_active",
        table_name="share_links",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("share_links")
