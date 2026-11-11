"""add kaali teeri tables

What and why: Kaali Teeri games are stored in `games` (kind 'kaali-teeri'); these tables hold what
each upload derives from them, so boards and stats never read the payload: `kt_hands` (one row per
done hand), `kt_game_players` (one per seat) and `kt_player_day` (per player per APP_TZ day).
Rollback: `alembic downgrade -1` drops the three tables; they are rebuilt from the stored payloads
by re-uploading (or rederiving) each Kaali Teeri game.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _game_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["game_id"], ["games.id"], name=op.f(f"fk_{table}_game_id"), ondelete="CASCADE"
    )


def _player_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], ["players.id"], name=op.f(f"fk_{table}_{column}"))


def _count(name: str) -> sa.Column[int]:
    return sa.Column(name, sa.Integer(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "kt_game_players",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        _count("points"),
        sa.Column("place", sa.SmallInteger(), nullable=True),
        sa.Column("won", sa.Boolean(), nullable=False),
        *(
            _count(c)
            for c in (
                "hands_bid",
                "bids_made",
                "times_partner",
                "partner_made",
                "double_slots",
                "sweeps",
            )
        ),
        sa.Column("first_done_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        _game_fk("kt_game_players"),
        _player_fk("kt_game_players", "player_id"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_kt_game_players")),
    )
    op.create_index(
        "ix_kt_game_players_player_id",
        "kt_game_players",
        ["player_id"],
        postgresql_include=["game_id", "points", "place", "last_done_at"],
    )
    op.create_index(
        "ix_kt_game_players_first_done_at",
        "kt_game_players",
        ["first_done_at"],
        postgresql_include=["player_id"],
    )
    op.create_index(
        "ix_kt_game_players_ended_at",
        "kt_game_players",
        ["ended_at"],
        postgresql_include=["player_id", "won"],
        postgresql_where=sa.text("ended_at IS NOT NULL"),
    )
    op.create_table(
        "kt_hands",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("hand_index", sa.Integer(), nullable=False),
        sa.Column("dealer_id", sa.Uuid(), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), nullable=False),
        sa.Column("bid", sa.SmallInteger(), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("trump", sa.Text(), nullable=False),
        sa.Column("partner_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("slots", postgresql.JSONB(), nullable=False),
        sa.Column("team_points", sa.SmallInteger(), nullable=False),
        sa.Column("made", sa.Boolean(), nullable=False),
        sa.Column("sweep", sa.Text(), nullable=False),
        sa.Column("scores", postgresql.JSONB(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sweep IN ('none', 'bidders', 'defenders')", name=op.f("ck_kt_hands_sweep")
        ),
        _game_fk("kt_hands"),
        _player_fk("kt_hands", "dealer_id"),
        _player_fk("kt_hands", "bidder_id"),
        sa.PrimaryKeyConstraint("game_id", "hand_index", name=op.f("pk_kt_hands")),
    )
    op.create_index("ix_kt_hands_done_at", "kt_hands", ["done_at"])
    op.create_table(
        "kt_player_day",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        *(
            _count(c)
            for c in (
                "games",
                "wins",
                "points",
                "hands_bid",
                "bids_made",
                "partner_hands",
                "partner_made",
            )
        ),
        _player_fk("kt_player_day", "player_id"),
        sa.PrimaryKeyConstraint("player_id", "day", name=op.f("pk_kt_player_day")),
    )
    op.create_index(
        "ix_kt_player_day_day",
        "kt_player_day",
        ["day"],
        postgresql_include=[
            "player_id",
            "games",
            "wins",
            "points",
            "hands_bid",
            "bids_made",
            "partner_hands",
            "partner_made",
        ],
    )


def downgrade() -> None:
    op.drop_table("kt_player_day")
    op.drop_table("kt_hands")
    op.drop_table("kt_game_players")
