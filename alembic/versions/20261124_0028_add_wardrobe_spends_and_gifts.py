"""add wardrobe looks, sikke spends and gifts

What and why: the Wardrobe, the Bazaar and gifts (rewards finish §3-§5). `sikke_spends` is the
append-only record of Sikke spent on items (bought or given); no recompute ever touches it, and a
player's balance is lifetime earned minus spent. `gifts` holds items sent to another player (the
receiver owns them at once), `wardrobe_looks` the worn look and `wardrobe_saved_looks` three saved
ones per player. The activity feed gains the `gift` kind.
Players erased with their account take their spends, gifts and looks with them (cascade).
Rollback: `alembic downgrade -1` drops the four tables (every purchase and gift is lost), deletes
`gift` feed items and restores the previous kind check.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVITY_OLD = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', 'night')"
)
_ACTIVITY_NEW = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown', 'night', "
    "'gift')"
)


def _swap_activity_check(condition: str) -> None:
    op.drop_constraint(op.f("ck_activity_kind"), "activity", type_="check")
    op.create_check_constraint(op.f("ck_activity_kind"), "activity", condition)


def _gifts() -> None:
    op.create_table(
        "gifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_player_id", sa.Uuid(), nullable=False),
        sa.Column("to_player_id", sa.Uuid(), nullable=False),
        sa.Column("sent_by", sa.Uuid(), nullable=True),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.SmallInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("variant >= 0", name=op.f("ck_gifts_variant")),
        # A deleted player (an erased account) takes its gifts with it, both ways.
        sa.ForeignKeyConstraint(
            ["from_player_id"],
            ["players.id"],
            name=op.f("fk_gifts_from_player_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_player_id"], ["players.id"], name=op.f("fk_gifts_to_player_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sent_by"], ["users.id"], name=op.f("fk_gifts_sent_by"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gifts")),
    )
    op.create_index(
        "ix_gifts_to_player_id_sent_at_id",
        "gifts",
        ["to_player_id", sa.literal_column("sent_at DESC"), sa.literal_column("id DESC")],
    )
    op.create_index(
        "ix_gifts_from_player_id_sent_at_id",
        "gifts",
        ["from_player_id", sa.literal_column("sent_at DESC"), sa.literal_column("id DESC")],
    )
    op.create_index(
        "ix_gifts_to_player_id_unopened",
        "gifts",
        ["to_player_id"],
        postgresql_where=sa.text("opened_at IS NULL"),
    )


def _spends() -> None:
    op.create_table(
        "sikke_spends",
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("gift_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('purchase', 'gift')", name=op.f("ck_sikke_spends_kind")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_sikke_spends_amount")),
        sa.CheckConstraint("variant >= 0", name=op.f("ck_sikke_spends_variant")),
        # A gift spend keeps its amount when the gift goes with its receiver (gift_id SET NULL).
        sa.CheckConstraint(
            "kind = 'gift' OR gift_id IS NULL", name=op.f("ck_sikke_spends_gift_id")
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_sikke_spends_player_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gift_id"], ["gifts.id"], name=op.f("fk_sikke_spends_gift_id"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sikke_spends")),
    )
    op.create_index(
        "uq_sikke_spends_player_id_idempotency_key",
        "sikke_spends",
        ["player_id", "idempotency_key"],
        unique=True,
    )
    op.create_index("ix_sikke_spends_gift_id", "sikke_spends", ["gift_id"])


def _looks() -> None:
    op.create_table(
        "wardrobe_looks",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("look", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_wardrobe_looks_player_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("player_id", name=op.f("pk_wardrobe_looks")),
    )
    op.create_table(
        "wardrobe_saved_looks",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("look", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("slot BETWEEN 0 AND 2", name=op.f("ck_wardrobe_saved_looks_slot")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_wardrobe_saved_looks_player_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("player_id", "slot", name=op.f("pk_wardrobe_saved_looks")),
    )


def upgrade() -> None:
    _gifts()
    _spends()
    _looks()
    _swap_activity_check(_ACTIVITY_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM activity WHERE kind = 'gift'")
    _swap_activity_check(_ACTIVITY_OLD)
    op.drop_table("wardrobe_saved_looks")
    op.drop_table("wardrobe_looks")
    op.drop_table("sikke_spends")
    op.drop_table("gifts")
