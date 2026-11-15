"""index players by creator

What and why: the server's circle of a user (who they may invite to a seat) starts from the players
they created, so `players.created_by` gets an index; the rest of the circle reads seat tables by
player and by game, which are indexed already.
Rollback: `alembic downgrade -1` drops the index; the circle query still works, scanning players.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_players_created_by", "players", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_players_created_by", table_name="players")
