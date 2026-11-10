"""The Ishaara key never reaches other games' reads: bootstraps, game lists and share links."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.ishaara_builder import (
    ClientFor,
    Table,
    client_for_fixture,  # noqa: F401 - fixture
    make_table,
    start,
)


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


@pytest.mark.parametrize(
    "path", ["/api/bootstrap", "/api/kaali-teeri/bootstrap", "/api/games", "/api/kaali-teeri/games"]
)
async def test_other_games_reads_never_carry_an_ishaara_game(table: Table, path: str) -> None:
    state, key = await start(table)

    for who in ("host", "sun"):
        response = await table.clients[who].get(path)
        assert response.status_code == 200, response.text
        assert state["id"] not in response.text
        assert ",".join(f'"{k}"' for k in key) not in response.text.replace(" ", "")


async def test_ishaara_games_get_no_share_links(table: Table) -> None:
    state, _ = await start(table)

    for kind in ("live", "recap"):
        response = await table.clients["host"].post(
            f"/api/games/{state['id']}/links", json={"kind": kind}
        )
        assert response.status_code == 404
