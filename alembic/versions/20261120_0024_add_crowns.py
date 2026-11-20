"""add crowns

What and why: crowns, status changes and bounties (rewards plan §6). `crowns` holds who holds each
title now (medals, Kursi, Sitara, Legend), `crown_reigns` every closed reign; a change posts a
`crown` feed item, and beating a holder pays a `bounty` Sikke entry. Fill both tables once with
`recompute-crowns`, then `recompute-sikke` for the bounties.
Rollback: `alembic downgrade -1` drops both tables, deletes `crown` feed items and `bounty`
entries, and restores the previous kind checks.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CROWN_KINDS = "kind IN ('medal1', 'medal2', 'medal3', 'kursi', 'sitara', 'legend')"
_GAMES = "game IN ('kachuful', 'spades', 'kaali-teeri', 'ishaara')"
_ACTIVITY_OLD = "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined')"
_ACTIVITY_NEW = (
    "kind IN ('achievement', 'game_finished', 'season_closed', 'player_joined', 'crown')"
)
_SIKKE_OLD = "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak')"
_SIKKE_NEW = (
    "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak', 'bounty')"
)


def _holders() -> sa.Column[list[object]]:
    return sa.Column("holders", postgresql.ARRAY(sa.Uuid()), nullable=False)


def _swap_check(table: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_kind"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_kind"), table, condition)


def upgrade() -> None:
    op.create_table(
        "crowns",
        sa.Column("game", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        _holders(),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("since_game", sa.Uuid(), nullable=True),
        sa.Column("defences", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(_CROWN_KINDS, name=op.f("ck_crowns_kind")),
        sa.CheckConstraint(_GAMES, name=op.f("ck_crowns_game")),
        sa.PrimaryKeyConstraint("game", "kind", name=op.f("pk_crowns")),
    )
    op.create_table(
        "crown_reigns",
        sa.Column("id", sa.Uuid(), server_default=sa.func.gen_random_uuid(), nullable=False),
        sa.Column("game", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        _holders(),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("since_game", sa.Uuid(), nullable=True),
        sa.Column("until_game", sa.Uuid(), nullable=True),
        sa.Column("defences", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(_CROWN_KINDS, name=op.f("ck_crown_reigns_kind")),
        sa.CheckConstraint(_GAMES, name=op.f("ck_crown_reigns_game")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crown_reigns")),
    )
    op.create_index(
        "ix_crown_reigns_game_kind_until",
        "crown_reigns",
        ["game", "kind", sa.literal_column("until DESC")],
    )
    _swap_check("activity", _ACTIVITY_NEW)
    _swap_check("sikke_entries", _SIKKE_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM sikke_entries WHERE kind = 'bounty'")
    op.execute("DELETE FROM activity WHERE kind = 'crown'")
    _swap_check("sikke_entries", _SIKKE_OLD)
    _swap_check("activity", _ACTIVITY_OLD)
    op.drop_table("crown_reigns")
    op.drop_table("crowns")
