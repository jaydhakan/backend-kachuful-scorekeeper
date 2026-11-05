"""add admin audit and anonymised players

What and why: admins can delete an account and everything it scored (contract change 2026-11-06).
`admin_audit` keeps one row per such action (who, what, when, the counts), since the data itself
is gone afterwards. `players.anonymised_at` marks a deleted account's player that other people's
games still use: it stays, nameless, so their results don't change.
Rollback: `alembic downgrade -1` drops the table and the column; anonymised players keep the
"Deleted player" name but lose the flag.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_admin_audit_actor_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit")),
    )
    op.add_column("players", sa.Column("anonymised_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("players", "anonymised_at")
    op.drop_table("admin_audit")
