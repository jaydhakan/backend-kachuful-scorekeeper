"""add game kind

What and why: the site hosts more than one game, so every game row says which one it is and
Kachuful's endpoints read only Kachuful rows. Existing rows are all Kachuful (the default).
The sync order index leads with `kind`, since every list now filters on it.
Rollback: `alembic downgrade -1` restores `ix_games_updated_at_id` and drops the column. It refuses
while rows of another kind exist, since they would silently become Kachuful games.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("kind", sa.Text(), server_default=sa.text("'kachuful'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_games_kind"), "games", "kind IN ('kachuful', 'kaali-teeri', 'ishaara')"
    )
    op.create_index("ix_games_kind_updated_at_id", "games", ["kind", "updated_at", "id"])
    op.drop_index("ix_games_updated_at_id", table_name="games")


def downgrade() -> None:
    others = op.get_bind().execute(sa.text("SELECT count(*) FROM games WHERE kind <> 'kachuful'"))
    if count := others.scalar_one():
        raise RuntimeError(f"{count} games of another kind would become Kachuful games")
    op.create_index("ix_games_updated_at_id", "games", ["updated_at", "id"])
    op.drop_index("ix_games_kind_updated_at_id", table_name="games")
    op.drop_constraint(op.f("ck_games_kind"), "games", type_="check")
    op.drop_column("games", "kind")
