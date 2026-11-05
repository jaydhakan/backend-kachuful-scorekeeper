"""add player aliases

What and why: a player can be known by more than one name (contract change 2026-11-05).
`player_aliases` holds the other names; each alias reserves its name key like a display name.
A unique index covers aliases among themselves, and a trigger on both tables refuses a name key
that another live player holds as display name or alias (an advisory lock per key closes races).
Rollback: `alembic downgrade -1` drops the triggers, the function and the table; every alias is
lost and the names they reserved become free.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESERVE_FUNCTION = """
CREATE FUNCTION reserve_player_name_key() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('player_name_key:' || NEW.name_key));
    IF TG_TABLE_NAME = 'players' THEN
        IF NEW.merged_into IS NULL AND EXISTS (
            SELECT 1 FROM player_aliases a
            WHERE a.name_key = NEW.name_key AND a.player_id <> NEW.id
        ) THEN
            RAISE EXCEPTION 'name key % is another player''s alias', NEW.name_key
                USING ERRCODE = 'unique_violation';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM players p
        WHERE p.name_key = NEW.name_key AND p.merged_into IS NULL AND p.id <> NEW.player_id
    ) THEN
        RAISE EXCEPTION 'name key % is another player''s name', NEW.name_key
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "player_aliases",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=op.f("fk_player_aliases_player_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_player_aliases_created_by")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_aliases")),
    )
    op.create_index("uq_player_aliases_name_key", "player_aliases", ["name_key"], unique=True)
    op.create_index("ix_player_aliases_player_id", "player_aliases", ["player_id"])
    op.execute(_RESERVE_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_players_reserve_name_key"
        " BEFORE INSERT OR UPDATE OF name_key, merged_into ON players"
        " FOR EACH ROW EXECUTE FUNCTION reserve_player_name_key()"
    )
    op.execute(
        "CREATE TRIGGER trg_player_aliases_reserve_name_key"
        " BEFORE INSERT OR UPDATE OF name_key, player_id ON player_aliases"
        " FOR EACH ROW EXECUTE FUNCTION reserve_player_name_key()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_player_aliases_reserve_name_key ON player_aliases")
    op.execute("DROP TRIGGER trg_players_reserve_name_key ON players")
    op.execute("DROP FUNCTION reserve_player_name_key()")
    op.drop_index("ix_player_aliases_player_id", table_name="player_aliases")
    op.drop_index("uq_player_aliases_name_key", table_name="player_aliases")
    op.drop_table("player_aliases")
