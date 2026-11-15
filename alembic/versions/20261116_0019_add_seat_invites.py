"""add seat invites

What and why: a host invites a signed-in person in their circle to a seat of a server-run game
(Spades online; Ishaara's clue-givers as a notification). `seat_invites` holds each invite and its
lifecycle; expiry is worked out on read, so there is no scheduler. One pending invite per person
per game (partial unique index); the invitee's pending list reads `(to_user, status)`.
Rollback: `alembic downgrade -1` drops the table; pending invites are lost, games are untouched.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user_fk(column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_seat_invites_{column}"), ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "seat_invites",
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("seat", sa.Text(), nullable=False),
        sa.Column("from_user", sa.Uuid(), nullable=False),
        sa.Column("to_user", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('ishaara', 'spades-online')", name=op.f("ck_seat_invites_kind")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled', 'expired')",
            name=op.f("ck_seat_invites_status"),
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_seat_invites_game_id"), ondelete="CASCADE"
        ),
        _user_fk("from_user"),
        _user_fk("to_user"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seat_invites")),
    )
    op.create_index(
        "uq_seat_invites_pending",
        "seat_invites",
        ["game_id", "to_user"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_seat_invites_to_user_status", "seat_invites", ["to_user", "status"])


def downgrade() -> None:
    op.drop_index("ix_seat_invites_to_user_status", table_name="seat_invites")
    op.drop_index("uq_seat_invites_pending", table_name="seat_invites")
    op.drop_table("seat_invites")
