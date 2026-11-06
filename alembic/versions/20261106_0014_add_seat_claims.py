"""add seat claims

What and why: a signed-in person can say "that seat is me" on a game whose player has no account;
the scorer (or the admin) approves, and the player merges into theirs, bringing its history along
(contract change 2026-11-06). One pending claim per player and claimant.
Rollback: `alembic downgrade -1` drops the table; pending claims are lost, approved ones already
merged.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seat_claims",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("claimant_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name=op.f("ck_seat_claims_status")
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_seat_claims_game_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_seat_claims_player_id")
        ),
        sa.ForeignKeyConstraint(
            ["claimant_user_id"],
            ["users.id"],
            name=op.f("fk_seat_claims_claimant_user_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_seat_claims_decided_by"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seat_claims")),
    )
    op.create_index(
        "uq_seat_claims_pending",
        "seat_claims",
        ["player_id", "claimant_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_seat_claims_game_id", "seat_claims", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_seat_claims_game_id", table_name="seat_claims")
    op.drop_index("uq_seat_claims_pending", table_name="seat_claims")
    op.drop_table("seat_claims")
