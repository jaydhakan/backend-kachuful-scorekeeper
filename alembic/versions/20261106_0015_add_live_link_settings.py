"""add live link settings

What and why: the scorer can switch spectator reactions off and keep a live table to signed-in
members (contract change 2026-11-06). Both are per link, so a new link starts from the defaults.
Rollback: `alembic downgrade -1` drops both columns; every link allows reactions and strangers.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "share_links",
        sa.Column("reactions", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "share_links",
        sa.Column("members_only", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("share_links", "members_only")
    op.drop_column("share_links", "reactions")
