"""add spades tables

What and why: Spades is stored as two game kinds. `spades` is a table-mode game the app uploads
(seats in `spades_seats`); `spades-online` is a table the server runs (`games.payload` is the public
state only): `spades_hands` holds the private hands, `spades_table_seats` each seat's account and
player, `spades_actions` the applied actions (audit and idempotency). `spades_game_players` is what
a finished game of either kind derives for boards and stats. The games kind CHECK gains both kinds,
and an open table's 4-letter code is unique (`uq_games_spades_code`).
Rollback: `alembic downgrade -1` drops the five tables and the index and restores the CHECK; it
refuses while Spades games exist (they would break the CHECK). Hands cannot be rebuilt, so a table
in play cannot be played after a rollback.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_BEFORE = "kind IN ('kachuful', 'kaali-teeri', 'ishaara')"
_KINDS_AFTER = "kind IN ('kachuful', 'kaali-teeri', 'ishaara', 'spades', 'spades-online')"
_ROW_COLUMNS = [
    "game_id",
    "kind",
    "seat",
    "team",
    "partner_id",
    "result",
    "score",
    "opp_score",
    "hands",
    "bids",
    "bids_made",
    "nils",
    "nils_made",
    "blind_nils",
    "blind_nils_made",
    "bags",
    "sets",
]
_COUNTS = (
    "hands",
    "bids",
    "bids_made",
    "nils",
    "nils_made",
    "blind_nils",
    "blind_nils_made",
    "bags",
    "sets",
)


def _game_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["game_id"], ["games.id"], name=op.f(f"fk_{table}_game_id"), ondelete="CASCADE"
    )


def _player_fk(table: str, column: str = "player_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], ["players.id"], name=op.f(f"fk_{table}_{column}"))


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["users.id"], name=op.f(f"fk_{table}_{column}"), ondelete="SET NULL"
    )


def _at(name: str, nullable: bool = True) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _seats() -> None:
    op.create_table(
        "spades_seats",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        _at("last_done_at"),
        _game_fk("spades_seats"),
        _player_fk("spades_seats"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_spades_seats")),
    )
    op.create_index(
        "ix_spades_seats_player_id",
        "spades_seats",
        ["player_id"],
        postgresql_include=["game_id", "last_done_at"],
    )
    op.create_table(
        "spades_table_seats",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        _at("last_done_at"),
        _at("last_seen_at"),
        _game_fk("spades_table_seats"),
        _player_fk("spades_table_seats"),
        _user_fk("spades_table_seats", "user_id"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_spades_table_seats")),
    )
    op.create_index(
        "uq_spades_table_seats_game_id_seat",
        "spades_table_seats",
        ["game_id", "seat"],
        unique=True,
        postgresql_where=sa.text("seat IS NOT NULL"),
    )
    op.create_index(
        "ix_spades_table_seats_player_id",
        "spades_table_seats",
        ["player_id"],
        postgresql_include=["game_id", "last_done_at"],
    )
    op.create_index(
        "ix_spades_table_seats_user_id",
        "spades_table_seats",
        ["user_id"],
        postgresql_include=["game_id", "seat"],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def _hands_and_actions() -> None:
    op.create_table(
        "spades_hands",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        sa.Column("cards", postgresql.JSONB(), nullable=False),
        _game_fk("spades_hands"),
        sa.PrimaryKeyConstraint("game_id", "seat", name=op.f("pk_spades_hands")),
    )
    op.create_table(
        "spades_actions",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        _at("at", nullable=False),
        sa.Column("by_user_id", sa.Uuid(), nullable=True),
        _game_fk("spades_actions"),
        _user_fk("spades_actions", "by_user_id"),
        sa.PrimaryKeyConstraint("game_id", "action_id", name=op.f("pk_spades_actions")),
    )
    op.create_index(
        "uq_spades_actions_game_id_seq", "spades_actions", ["game_id", "seq"], unique=True
    )


def _game_players() -> None:
    op.create_table(
        "spades_game_players",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("seat", sa.SmallInteger(), nullable=False),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("ended_by", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("opp_score", sa.Integer(), nullable=False),
        *(sa.Column(c, sa.Integer(), nullable=False) for c in _COUNTS),
        _at("finished_at", nullable=False),
        sa.CheckConstraint(
            "kind IN ('spades', 'spades-online')", name=op.f("ck_spades_game_players_kind")
        ),
        sa.CheckConstraint("team IN ('a', 'b')", name=op.f("ck_spades_game_players_team")),
        sa.CheckConstraint(
            "result IN ('win', 'loss', 'draw')", name=op.f("ck_spades_game_players_result")
        ),
        _game_fk("spades_game_players"),
        _player_fk("spades_game_players"),
        _player_fk("spades_game_players", "partner_id"),
        sa.PrimaryKeyConstraint("game_id", "player_id", name=op.f("pk_spades_game_players")),
    )
    op.create_index(
        "ix_spades_game_players_player_id",
        "spades_game_players",
        ["player_id"],
        postgresql_include=[*_ROW_COLUMNS, "finished_at"],
    )
    op.create_index(
        "ix_spades_game_players_finished_at",
        "spades_game_players",
        ["finished_at"],
        postgresql_include=["player_id", *_ROW_COLUMNS],
    )


def upgrade() -> None:
    op.drop_constraint(op.f("ck_games_kind"), "games", type_="check")
    op.create_check_constraint(op.f("ck_games_kind"), "games", _KINDS_AFTER)
    op.create_index(
        "uq_games_spades_code",
        "games",
        [sa.text("(summary ->> 'code')")],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'spades-online' AND status = 'active' AND deleted_at IS NULL"
        ),
    )
    _seats()
    _hands_and_actions()
    _game_players()


def downgrade() -> None:
    spades = op.get_bind().execute(
        sa.text("SELECT count(*) FROM games WHERE kind IN ('spades', 'spades-online')")
    )
    if count := spades.scalar_one():
        raise RuntimeError(f"{count} Spades games would break the games kind CHECK")
    op.drop_table("spades_game_players")
    op.drop_table("spades_actions")
    op.drop_table("spades_hands")
    op.drop_table("spades_table_seats")
    op.drop_table("spades_seats")
    op.drop_index("uq_games_spades_code", table_name="games")
    op.drop_constraint(op.f("ck_games_kind"), "games", type_="check")
    op.create_check_constraint(op.f("ck_games_kind"), "games", _KINDS_BEFORE)
