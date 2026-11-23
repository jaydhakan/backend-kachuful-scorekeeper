"""drop the Kursi from the kind checks

What and why: after 0026 removed the Kursi's rows, the `sikke_entries`, `crowns` and `crown_reigns`
kind checks no longer allow its values.
Rollback: `alembic downgrade -1` restores the previous checks.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CROWN_OLD = "kind IN ('medal1', 'medal2', 'medal3', 'kursi', 'sitara', 'legend')"
_CROWN_NEW = "kind IN ('medal1', 'medal2', 'medal3', 'sitara', 'legend')"
_SIKKE_OLD = (
    "kind IN ('finish', 'win', 'achievement', 'kursi_take', 'kursi_defence', 'streak', 'bounty', "
    "'sitara')"
)
_SIKKE_NEW = "kind IN ('finish', 'win', 'achievement', 'streak', 'bounty', 'sitara')"


def _swap_check(table: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_kind"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_kind"), table, condition)


def upgrade() -> None:
    _swap_check("sikke_entries", _SIKKE_NEW)
    _swap_check("crowns", _CROWN_NEW)
    _swap_check("crown_reigns", _CROWN_NEW)


def downgrade() -> None:
    _swap_check("crown_reigns", _CROWN_OLD)
    _swap_check("crowns", _CROWN_OLD)
    _swap_check("sikke_entries", _SIKKE_OLD)
