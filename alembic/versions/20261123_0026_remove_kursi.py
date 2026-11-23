"""remove the Kursi's data

What and why: the Kursi (the chair) is gone from the app and the API. This deletes what it left:
`kursi_take` and `kursi_defence` Sikke entries, `kursi` crowns and reigns, and `crown` feed items
about it; strips `kursi` from closed nights' stories and re-sums their `sikke` from what remains.
0027 then drops the Kursi from the kind checks. After deploy run `recompute-sikke` (bounties paid
for beating a Kursi holder go).
Rollback: `alembic downgrade -1` is a no-op; the deleted rows are not restored (they were derived
from the games, and the code that derived them is gone).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A closed night's `sikke` is each player's pay from its games plus its Sitara (NightsService._row).
_NIGHT_SIKKE = """
UPDATE nights n SET data = (n.data - 'kursi') || jsonb_build_object('sikke', COALESCE((
    SELECT jsonb_object_agg(t.player_id::text, t.total)
    FROM (
        SELECT e.player_id, sum(e.amount) AS total
        FROM sikke_entries e
        WHERE e.game_id = ANY(n.games) OR e.key = 'sitara:' || n.id::text
        GROUP BY e.player_id
    ) t
), '{}'::jsonb))
WHERE n.data ? 'kursi'
"""


def upgrade() -> None:
    op.execute("DELETE FROM sikke_entries WHERE kind IN ('kursi_take', 'kursi_defence')")
    op.execute("DELETE FROM crowns WHERE kind = 'kursi'")
    op.execute("DELETE FROM crown_reigns WHERE kind = 'kursi'")
    op.execute("DELETE FROM activity WHERE kind = 'crown' AND data->>'crown' = 'kursi'")
    op.execute(_NIGHT_SIKKE)


def downgrade() -> None:
    # Nothing to restore: the deleted rows were derived data and nothing would rebuild them.
    pass
