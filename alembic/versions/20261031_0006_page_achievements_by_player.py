"""page achievements by player

What and why: the per-player achievement list pages by (earned_at, id); the index now carries id
too, so a page is one index range scan with no sort.
Rollback: `alembic downgrade -1` restores the (player_id, earned_at) index; nothing is lost.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_achievements_player_id_earned_at", table_name="achievements")
    op.create_index(
        "ix_achievements_player_id_earned_at",
        "achievements",
        ["player_id", sa.literal_column("earned_at DESC"), sa.literal_column("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_achievements_player_id_earned_at", table_name="achievements")
    op.create_index(
        "ix_achievements_player_id_earned_at",
        "achievements",
        ["player_id", sa.literal_column("earned_at DESC")],
    )
