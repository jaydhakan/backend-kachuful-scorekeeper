"""index active games by owner

What and why: `GET /api/games?status=active&mine=true&limit=1` finds a user's newest unfinished
game for the Home screen's resume card (contract change 2026-11-01). A partial index over the
active, unended, undeleted games keeps that one index read however many games a user has.
Rollback: `alembic downgrade -1` drops the index; the query still works through
`ix_games_owner_id_updated_at`, scanning past finished games.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_games_owner_id_active",
        "games",
        ["owner_id", sa.text("updated_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_games_owner_id_active", table_name="games")
