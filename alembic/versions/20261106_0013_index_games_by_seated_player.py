"""index games by seated player

What and why: History lists every game the caller sat in, not only the ones they scored
(contract change 2026-11-06, `GET /api/games?involving=true`). The seat is matched on the stored
payload, which deleted games keep, so their tombstones still reach the app; a GIN index over
`payload -> 'players'` keeps that containment test an index read.
Rollback: `alembic downgrade -1` drops the index; the query still works, scanning games.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_games_payload_players ON games "
        "USING gin ((payload -> 'players') jsonb_path_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_games_payload_players", table_name="games")
