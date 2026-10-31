"""add games and derived tables

What and why: B3/B4 game storage. `games` keeps the app's JSON whole; `game_players`, `round_scores`
and `player_day` are derived from it on every upload so reads never parse payloads (plan §15.3).
Rollback: `alembic downgrade -1` drops all four tables and every uploaded game with them.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("rev", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("player_count", sa.SmallInteger(), nullable=False),
        sa.Column("round_count", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_ended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'finished')", name=op.f("ck_games_status")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_games_owner_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_games")),
    )
    op.create_index(
        "ix_games_auto_end",
        "games",
        ["client_updated_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_games_ended_at_live",
        "games",
        ["ended_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_games_owner_id_updated_at",
        "games",
        ["owner_id", sa.literal_column("updated_at DESC")],
        unique=False,
    )
    op.create_index("ix_games_updated_at_id", "games", ["updated_at", "id"], unique=False)
    op.create_table(
        "player_day",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("made", sa.Integer(), nullable=False),
        sa.Column("missed", sa.Integer(), nullable=False),
        sa.Column("best_round", sa.Integer(), nullable=True),
        sa.Column("worst_round", sa.Integer(), nullable=True),
        sa.Column("max_hot", sa.Integer(), nullable=False),
        sa.Column("max_cold", sa.Integer(), nullable=False),
        sa.Column("hook_hits", sa.Integer(), nullable=False),
        sa.Column("games", sa.Integer(), nullable=False),
        sa.Column("games_ended", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_player_day_player_id")
        ),
        sa.PrimaryKeyConstraint("player_id", "day", name=op.f("pk_player_day")),
    )
    op.create_index(
        "ix_player_day_day",
        "player_day",
        ["day"],
        unique=False,
        postgresql_include=[
            "player_id",
            "rounds",
            "points",
            "made",
            "wins",
            "best_round",
            "worst_round",
            "max_hot",
            "max_cold",
            "hook_hits",
        ],
    )
    op.create_table(
        "game_players",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("local_id", sa.Text(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("name_snapshot", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("place", sa.SmallInteger(), nullable=True),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("last", sa.Boolean(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("made", sa.Integer(), nullable=False),
        sa.Column("missed", sa.Integer(), nullable=False),
        sa.Column("best_round", sa.Integer(), nullable=True),
        sa.Column("worst_round", sa.Integer(), nullable=True),
        sa.Column("longest_hot", sa.Integer(), nullable=False),
        sa.Column("longest_cold", sa.Integer(), nullable=False),
        sa.Column("hook_hits", sa.Integer(), nullable=False),
        sa.Column("first_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_game_players_game_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_game_players_player_id")
        ),
        sa.PrimaryKeyConstraint("game_id", "local_id", name=op.f("pk_game_players")),
    )
    op.create_index(
        "ix_game_players_ended_at",
        "game_players",
        ["ended_at"],
        unique=False,
        postgresql_include=["player_id", "place", "won"],
        postgresql_where=sa.text("ended_at IS NOT NULL"),
    )
    op.create_index(
        "ix_game_players_last_done_at",
        "game_players",
        ["last_done_at"],
        unique=False,
        postgresql_include=["player_id", "first_done_at"],
    )
    op.create_index(
        "ix_game_players_player_id",
        "game_players",
        ["player_id"],
        unique=False,
        postgresql_include=[
            "game_id",
            "points",
            "place",
            "won",
            "rounds",
            "first_done_at",
            "last_done_at",
            "ended_at",
        ],
    )
    op.create_table(
        "round_scores",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cards", sa.SmallInteger(), nullable=False),
        sa.Column("max_cards", sa.SmallInteger(), nullable=False),
        sa.Column("trump", sa.Text(), nullable=False),
        sa.Column("bid", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("is_dealer", sa.Boolean(), nullable=False),
        sa.Column("hook_hits", sa.SmallInteger(), nullable=False),
        sa.Column("run_hot", sa.Integer(), nullable=False),
        sa.Column("run_cold", sa.Integer(), nullable=False),
        sa.Column("table_size", sa.SmallInteger(), nullable=False),
        sa.Column("made_count", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_round_scores_game_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_round_scores_player_id")
        ),
        sa.PrimaryKeyConstraint(
            "game_id", "round_index", "player_id", name=op.f("pk_round_scores")
        ),
    )
    op.create_index("ix_round_scores_done_at", "round_scores", ["done_at"], unique=False)
    op.create_index(
        "ix_round_scores_player_id_done_at",
        "round_scores",
        ["player_id", "done_at"],
        unique=False,
        postgresql_include=[
            "points",
            "outcome",
            "run_hot",
            "run_cold",
            "hook_hits",
            "bid",
            "cards",
            "trump",
            "is_dealer",
        ],
    )
    op.create_foreign_key(
        op.f("fk_users_player_id"), "users", "players", ["player_id"], ["id"], use_alter=True
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_users_player_id"), "users", type_="foreignkey")
    op.drop_index(
        "ix_round_scores_player_id_done_at",
        table_name="round_scores",
        postgresql_include=[
            "points",
            "outcome",
            "run_hot",
            "run_cold",
            "hook_hits",
            "bid",
            "cards",
            "trump",
            "is_dealer",
        ],
    )
    op.drop_index("ix_round_scores_done_at", table_name="round_scores")
    op.drop_table("round_scores")
    op.drop_index(
        "ix_game_players_player_id",
        table_name="game_players",
        postgresql_include=[
            "game_id",
            "points",
            "place",
            "won",
            "rounds",
            "first_done_at",
            "last_done_at",
            "ended_at",
        ],
    )
    op.drop_index(
        "ix_game_players_last_done_at",
        table_name="game_players",
        postgresql_include=["player_id", "first_done_at"],
    )
    op.drop_index(
        "ix_game_players_ended_at",
        table_name="game_players",
        postgresql_include=["player_id", "place", "won"],
        postgresql_where=sa.text("ended_at IS NOT NULL"),
    )
    op.drop_table("game_players")
    op.drop_index(
        "ix_player_day_day",
        table_name="player_day",
        postgresql_include=[
            "player_id",
            "rounds",
            "points",
            "made",
            "wins",
            "best_round",
            "worst_round",
            "max_hot",
            "max_cold",
            "hook_hits",
        ],
    )
    op.drop_table("player_day")
    op.drop_index("ix_games_updated_at_id", table_name="games")
    op.drop_index("ix_games_owner_id_updated_at", table_name="games")
    op.drop_index(
        "ix_games_ended_at_live", table_name="games", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "ix_games_auto_end",
        table_name="games",
        postgresql_where=sa.text("status = 'active' AND ended_at IS NULL AND deleted_at IS NULL"),
    )
    op.drop_table("games")
