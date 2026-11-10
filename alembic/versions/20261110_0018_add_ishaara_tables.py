"""add ishaara tables

What and why: Ishaara games are run by the server. `games` (kind 'ishaara') holds the public state;
`ishaara_keys` holds each game's key apart from it, `ishaara_roles` every seat (who may read the key
and send which action), `ishaara_actions` the applied actions (audit and idempotency), and
`ishaara_game_players` / `ishaara_player_day` what a finished game derives for boards and stats.
Rollback: `alembic downgrade -1` drops the five tables. Keys and roles cannot be rebuilt from the
public state, so any Ishaara game in progress can no longer be played after a rollback.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROW_COLUMNS = [
    "game_id",
    "team",
    "role",
    "won",
    "words_found",
    "wrong_picks",
    "trap_hit",
    "clues_given",
    "avg_clue_number",
    "penalties",
]


def _game_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["game_id"], ["games.id"], name=op.f(f"fk_{table}_game_id"), ondelete="CASCADE"
    )


def _player_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["player_id"], ["players.id"], name=op.f(f"fk_{table}_player_id")
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}"), ondelete="SET NULL"
    )


def _int(name: str, small: bool = False) -> sa.Column[int]:
    return sa.Column(name, sa.SmallInteger() if small else sa.Integer(), nullable=False)


def _keys_and_roles() -> None:
    op.create_table(
        "ishaara_keys",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("key", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _game_fk("ishaara_keys"),
        sa.PrimaryKeyConstraint("game_id", name=op.f("pk_ishaara_keys")),
    )
    op.create_table(
        "ishaara_roles",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.CheckConstraint("team IN ('sun', 'moon')", name=op.f("ck_ishaara_roles_team")),
        sa.CheckConstraint("role IN ('clue-giver', 'guesser')", name=op.f("ck_ishaara_roles_role")),
        _game_fk("ishaara_roles"),
        _player_fk("ishaara_roles"),
        _user_fk("ishaara_roles", "user_id"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_ishaara_roles")),
    )
    op.create_index(
        "uq_ishaara_roles_clue_giver",
        "ishaara_roles",
        ["game_id", "team"],
        unique=True,
        postgresql_where=sa.text("role = 'clue-giver'"),
    )
    op.create_index(
        "ix_ishaara_roles_player_id",
        "ishaara_roles",
        ["player_id"],
        postgresql_include=["game_id"],
    )
    op.create_index(
        "ix_ishaara_roles_user_id_clue_giver",
        "ishaara_roles",
        ["user_id"],
        postgresql_include=["game_id", "team"],
        postgresql_where=sa.text("role = 'clue-giver'"),
    )


def _actions() -> None:
    op.create_table(
        "ishaara_actions",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by_user_id", sa.Uuid(), nullable=True),
        _game_fk("ishaara_actions"),
        _user_fk("ishaara_actions", "by_user_id"),
        sa.PrimaryKeyConstraint("game_id", "action_id", name=op.f("pk_ishaara_actions")),
    )
    op.create_index(
        "uq_ishaara_actions_game_id_seq", "ishaara_actions", ["game_id", "seq"], unique=True
    )


def _derived() -> None:
    op.create_table(
        "ishaara_game_players",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("won", sa.Boolean(), nullable=False),
        _int("words_found", small=True),
        _int("wrong_picks", small=True),
        sa.Column("trap_hit", sa.Boolean(), nullable=False),
        _int("clues_given", small=True),
        sa.Column("avg_clue_number", sa.Double(), nullable=True),
        _int("penalties", small=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        _game_fk("ishaara_game_players"),
        _player_fk("ishaara_game_players"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_ishaara_game_players")),
    )
    op.create_index(
        "ix_ishaara_game_players_player_id",
        "ishaara_game_players",
        ["player_id"],
        postgresql_include=[*_ROW_COLUMNS, "finished_at"],
    )
    op.create_index(
        "ix_ishaara_game_players_finished_at",
        "ishaara_game_players",
        ["finished_at"],
        postgresql_include=["player_id", *_ROW_COLUMNS],
    )
    op.create_table(
        "ishaara_player_day",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        *(
            _int(c)
            for c in (
                "games",
                "wins",
                "words_found",
                "wrong_picks",
                "trap_hits",
                "clue_games",
                "clue_wins",
                "clues_given",
            )
        ),
        sa.Column("avg_clue_sum", sa.Double(), nullable=False),
        _int("avg_clue_games"),
        _int("penalties"),
        _player_fk("ishaara_player_day"),
        sa.PrimaryKeyConstraint("player_id", "day", name=op.f("pk_ishaara_player_day")),
    )
    op.create_index("ix_ishaara_player_day_day", "ishaara_player_day", ["day"])


def upgrade() -> None:
    _keys_and_roles()
    _actions()
    _derived()


def downgrade() -> None:
    op.drop_table("ishaara_player_day")
    op.drop_table("ishaara_game_players")
    op.drop_table("ishaara_actions")
    op.drop_table("ishaara_roles")
    op.drop_table("ishaara_keys")
