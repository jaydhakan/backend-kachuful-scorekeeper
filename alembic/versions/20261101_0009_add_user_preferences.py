"""add user preferences

What and why: per-player theme and setup defaults that follow a user across devices (contract
change 2026-11-01). `users.preferences` holds `{theme?, setup?}`; `preferences_updated_at` lets
the app keep whichever of its own and the server's copy is newer. Existing users get `{}`.
Rollback: `alembic downgrade -1` drops both columns; every saved preference is lost.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("preferences_updated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "preferences_updated_at")
    op.drop_column("users", "preferences")
