"""add extensions and app_state

What and why: enables citext (case-insensitive emails) and pg_trgm (player name search), and adds
the one-row app_state table holding the global data version used in ETags and the last backup time.
Rollback: `alembic downgrade -1` drops app_state and both extensions; nothing else depends on them yet.

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "app_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("data_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_app_state_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_state")),
    )
    op.execute("INSERT INTO app_state (id) VALUES (1)")


def downgrade() -> None:
    op.drop_table("app_state")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS citext")
